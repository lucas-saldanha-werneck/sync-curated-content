"""Regressões da auditoria de 12/11/2026 (ids SCC-xx). Cada teste falha no código anterior à correção."""
import functools, json, os, shutil, subprocess, sys, time
from pathlib import Path

import pytest

from lib.applier import marker, plan_ops
from lib.config import Config
from lib.fetch_types import FetchResult
from lib.journal import Journal
from lib.records import Records
from lib.snapshot import Snap
from tests import iso_helpers
from tests.test_capture import FakeRD
from tests.test_dropbox_store import make as make_store

ROOT = Path(__file__).resolve().parents[1]
OUT = json.loads((ROOT / "tests/fixtures/claude_output.json").read_text())
ITEMS = {1: {"link": "https://fonte.com/a", "title": "Fonte A", "raw": "raw/curated/2026-09-10_youtube-fonte-a-r1.md"}}
PAGE = b"# Energia\n\n## Tarifa\n\ntexto antigo\n\n## Outra\n"
VOCAB = {"inversor", "solar"}
ENV = {"RAINDROP_TOKEN": "t", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s",
       "GROQ_API_KEY": "g", "CLAUDE_CODE_OAUTH_TOKEN": "c"}


def _cli():
    sys.path.insert(0, str(ROOT / "bin"))
    import sync as cli
    return cli


def _app(tmp_path, monkeypatch, items=(), fetch_fn=None, store=None):
    """App de verdade com Raindrop/Dropbox/fetch falsos e sem sondas externas."""
    cli = _cli()
    from lib.config import load_config
    store = store or make_store()

    class RD(FakeRD):
        def list_all(self, since_iso=None, **k): return list(items)
    monkeypatch.setattr(cli, "make_raindrop", lambda cfg: RD(items[0] if items else {"id": 0, "tags": []}))
    monkeypatch.setattr(cli, "make_store", lambda cfg, env: store)
    monkeypatch.setattr(cli, "make_fetch_fn", lambda: fetch_fn or (lambda it, work, cfg, secrets, journal:
                        FetchResult(platform="article", text="t" * 500, method="trafilatura")))
    monkeypatch.setattr(cli, "claude_auth_probe", lambda cfg, tok: (True, ""))
    env = {**ENV, "WORK_DIR": str(tmp_path), "HEALTH_PUBLIC_DIR": str(tmp_path / "public")}
    cfg = load_config(ROOT / "config.json", env)
    return cli, cli.App(cfg, env), store


def _item(iid):
    return {"id": iid, "link": f"https://blog.com/{iid}", "title": f"T{iid}", "excerpt": "", "note": "", "tags": [],
            "type": "article", "created": f"2026-09-10T10:00:{iid % 60:02d}.000Z", "cover": "", "domain": "blog.com",
            "raw": {"_id": iid}}


# ---------- SCC-01: item sem catálogo não entra no índice do hook ----------
def test_scc01_catalog_index_skips_items_without_catalog(tmp_path, monkeypatch):
    cli, app, store = _app(tmp_path, monkeypatch)
    rec = app.records
    rec.write(3, "meta", {"title": "pulado"}); rec.write(3, "distill", {"decision": "skip", "catalog": None, "date": "2026-11-12"})
    rec.write(4, "meta", {"title": "bom"})
    rec.write(4, "distill", {"decision": "wiki", "date": "2026-11-12",
                             "catalog": {"type": "skill", "triggers": ["video", "youtube"]}})
    app.health(rec.refresh())
    lines = [json.loads(l) for l in (tmp_path / "catalog-index.jsonl").read_text().splitlines()]
    assert [l["id"] for l in lines] == [4]
    assert all(isinstance(l["triggers"], list) for l in lines)


# ---------- SCC-02: item desfeito não volta ao catálogo ----------
def test_scc02_undone_item_stays_out_of_catalog(tmp_path):
    from lib.batch import run_batch
    from lib.undo import undo_run
    from tests.test_batch import make_deps
    deps, store, rd = make_deps(tmp_path)
    res = run_batch([1], deps, tmp_path / "work"); assert res["status"] == "ok"
    deps.records.refresh(); assert [e["id"] for e in deps.records.catalog_entries()] == [1]
    undo_run(res["run_id"], store, deps.journal, deps.cfg)
    deps.records.refresh()
    assert [e for e in deps.records.catalog_entries() if e["id"] == 1] == []    # o próximo lote não regera a linha
    assert deps.records.state(1) == "wiki"                                        # e o item não volta sozinho à fila
    assert deps.records.request_redo(1) == "redo1"                                # retry depois do undo segue valendo


# ---------- SCC-03: `now` dispensa a espera do lote ----------
def test_scc03_now_request_runs_a_batch_without_waiting(tmp_path, monkeypatch):
    cli, app, store = _app(tmp_path, monkeypatch)
    app.records.write(7, "meta", {"title": "x"}); app.records.write(7, "captured", {"raw": "raw/curated/x-r7.md"})
    calls = []
    monkeypatch.setattr(cli, "run_batch", lambda ids, deps, work: calls.append(list(ids)) or {"run_id": "r", "status": "distill_invalid"})
    app.cycle()
    assert calls == []                         # 1 item < batch_min_items e acabou de chegar: espera
    app.journal.enqueue_request("now", None)
    app.cycle()
    assert calls == [[7]]


# ---------- SCC-04: vídeo do Instagram conta no teto do Groq ----------
def test_scc04_instagram_video_groq_seconds_are_counted(tmp_path):
    from lib.fetcher import fetch
    seen = {}

    def ig(url, work, cfg, apify, groq, **k):
        seen.update(k)
        return FetchResult(platform="instagram", method="apify", text="x", duration_s=30, extra={"video_method": "groq"})
    j = Journal(tmp_path / "j.db"); cfg = Config()
    j.add_groq_seconds(100)
    fetch({"id": 1, "link": "https://www.instagram.com/reel/a/", "type": "link"}, tmp_path / "w", cfg, {"groq": "g", "apify": "a"},
          journal=j, fns={"instagram": ig}, isolate=False)
    assert seen.get("groq_seconds_used_today") == 100
    assert j.count_groq_seconds_today(cfg.tz) == 130
    j.close()


def test_scc04_fetch_instagram_passes_daily_usage_to_video(tmp_path):
    from lib.fetch_social import fetch_instagram
    seen = {}
    opener = lambda u, **k: (json.dumps([{"caption": "c", "videoUrl": "https://cdn/v.mp4"}]).encode(), u, {})

    def vid(url, work, cfg, groq, plat, **k):
        seen.update(k); return FetchResult(platform="instagram", method="groq", text="t", duration_s=5)
    fetch_instagram("https://instagram.com/p/1", tmp_path, Config(), "tok", "g", opener=opener, video_fn=vid,
                    groq_seconds_used_today=777, url_check=lambda u: ("cdn", "1.1.1.1"))
    assert seen.get("groq_seconds_used_today") == 777


# ---------- SCC-05: timeout do claude mata o processo e os netos ----------
def test_scc05_claude_timeout_kills_the_whole_tree(tmp_path, monkeypatch):
    if not shutil.which("timeout"):
        pytest.skip("coreutils timeout ausente neste sistema")
    from lib.distill import run_claude, DistillError
    bindir = tmp_path / "bin"; bindir.mkdir()
    pidfile = tmp_path / "neto.pid"
    fake = bindir / "claude"
    fake.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n"); fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    ws = tmp_path / "ws"; ws.mkdir()
    t0 = time.monotonic()
    with pytest.raises(DistillError, match="timeout"):
        run_claude(ws, Config(claude_timeout_s=1), "tok", "p", ROOT / "schema/distill.json")
    assert time.monotonic() - t0 < 15
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    os.kill(pid, 9)
    pytest.fail("o neto do claude ficou órfão depois do timeout")


# ---------- SCC-06: retry troca as claims antigas do item ----------
def test_scc06_new_run_replaces_old_claims_of_the_item():
    old = b"<!-- curated:r1 2026-09-10 -->\n- claim velha ([raw](../raw/curated/old-r1.md))\n"     # formato já gravado no vault
    page = PAGE.replace(b"## Tarifa\n\n", b"## Tarifa\n\n" + old)
    other = b"# Outra\n\n" + old + b"fim\n"
    s = Snap({"wiki/energia-e-recarga.md": (page, "rev1"), "wiki/outra.md": (other, "rev2"), "CLAUDE.md": (b"", "r0")})
    m = plan_ops(OUT, s, ITEMS, Config(), "run2", "2026-09-11", VOCAB)
    ops = {o.path: o.after for o in m.ops}
    assert b"claim velha" not in ops["wiki/energia-e-recarga.md"]
    assert ops["wiki/energia-e-recarga.md"].count(b"<!-- curated:r1 ") == 2 and b"22kW" in ops["wiki/energia-e-recarga.md"]
    assert ops["wiki/outra.md"] == b"# Outra\n\nfim\n"                 # página que a saída nova não tocou também perde a velha
    assert m.per_item[1]["pages"] == ["wiki/energia-e-recarga.md"]


# ---------- SCC-07: backfill respeita o teto e sobrevive a captura com erro ----------
def test_scc07_backfill_obeys_daily_cap_and_survives_capture_errors(tmp_path, monkeypatch, capsys):
    items = [_item(i) for i in range(1, 6)]
    cli, app, store = _app(tmp_path, monkeypatch, items=items)
    for _ in range(app.cfg.max_batches_per_day * 2):                     # teto do dia (inclusive a janela das 3h) esgotado
        rid = app.journal.start_run("distill", []); app.journal.finish_run(rid, "ok", {})
    real = cli.capture_one

    def flaky(it, deps, work):
        if it["id"] == 3: raise RuntimeError("captura explodiu")
        return real(it, deps, work)
    monkeypatch.setattr(cli, "capture_one", flaky)
    calls = []
    monkeypatch.setattr(cli, "run_batch", lambda ids, deps, work: calls.append(ids) or {"run_id": "r", "status": "ok"})
    assert app.backfill(None, dry=False, cont=True) == 0
    assert calls == []
    assert "teto" in capsys.readouterr().out
    recs = app.records.refresh()
    assert sum(1 for i in (1, 2, 4, 5) if "captured" in recs.get(i, set())) == 4   # o erro do r3 não parou os outros


# ---------- SCC-08: re-plan de página nova já criada segue com catálogo e log ----------
def test_scc08_replan_after_page_created_keeps_catalog_and_log():
    out = json.loads(json.dumps(OUT))
    out["items"][0]["new_page"] = {"name": "inversor-x", "summary": "Resumo.", "body": "Corpo da página."}
    created = f"# inversor-x\n\nResumo.\n\n{marker(1, '2026-09-11', 'run1')}\nCorpo da página.\n".encode()
    s = Snap({"wiki/energia-e-recarga.md": (PAGE, "rev1"), "wiki/inversor-x.md": (created, "rev9"), "CLAUDE.md": (b"", "r0")})
    m = plan_ops(out, s, ITEMS, Config(), "run1", "2026-09-11", VOCAB)
    paths = {o.path for o in m.ops}
    assert m.per_item[1]["decision"] == "wiki"
    assert "wiki/inversor-x.md" in m.per_item[1]["pages"] and "wiki/inversor-x.md" not in paths
    assert "wiki/ferramentas-e-ideias.md" in paths and "wiki/log/2026-09_curated.md" in paths


# ---------- SCC-09: página fora do padrão do schema não passa nem pelo fallback ----------
@pytest.mark.parametrize("page", ["CLAUDE.md", "wiki/ferramentas-e-ideias.md", "wiki/log/2026-09_curated.md"])
def test_scc09_claim_page_must_match_schema_pattern(page):
    out = json.loads(json.dumps(OUT))
    out["items"][0]["claims"] = [{"page": page, "anchor": "", "text": "Uma afirmação qualquer com tamanho."}]
    s = Snap({"wiki/energia-e-recarga.md": (PAGE, "rev1"), "CLAUDE.md": (b"regras\n", "r0"),
              "wiki/ferramentas-e-ideias.md": (b"# cat\n", "r1"), "wiki/log/2026-09_curated.md": (b"# log\n", "r2")})
    m = plan_ops(out, s, ITEMS, Config(), "run1", "2026-09-11", VOCAB)
    assert m.per_item[1]["decision"] == "retry"
    assert m.ops == []                                                 # item em retry não grava nada


def test_scc09_new_page_name_must_match_schema_pattern():
    out = json.loads(json.dumps(OUT))
    out["items"][0]["new_page"] = {"name": "../CLAUDE", "summary": "s", "body": "b"}
    m = plan_ops(out, Snap({"wiki/energia-e-recarga.md": (PAGE, "rev1")}), ITEMS, Config(), "run1", "2026-09-11", VOCAB)
    assert m.per_item[1]["decision"] == "retry"


# ---------- SCC-10: roteamento pelo host e guarda anti-SSRF antes do yt-dlp ----------
def test_scc10_route_uses_the_hostname():
    from lib.fetcher import route
    assert route("https://evil.example/?u=youtube.com", "link") == "article"
    assert route("http://10.0.0.1/youtube.com/watch", "link") == "article"
    assert route("https://notyoutube.com/x", "link") == "article"
    assert route("https://m.youtube.com/watch?v=1", "link") == "youtube"
    assert route("https://youtu.be/x", "link") == "youtube"


def test_scc10_blocked_url_never_reaches_ytdlp(tmp_path, monkeypatch):
    from lib import fetcher
    from lib.net_guard import BlockedURL
    called = []

    def blocked(url): raise BlockedURL(f"{url} resolve para IP não público")
    monkeypatch.setattr(fetcher, "check_url", blocked)
    r = fetcher.fetch({"id": 1, "link": "https://www.youtube.com/watch?v=1", "type": "video"}, tmp_path, Config(), {}, None,
                      fns={"video": lambda *a, **k: called.append(a)}, isolate=False)
    assert r.method == "none" and "bloqueada" in r.error and called == []


# ---------- SCC-11: worker isolado não herda memória nem segredos ----------
def test_scc11_isolated_worker_gets_no_parent_memory_or_secrets(tmp_path, monkeypatch):
    from lib.fetcher import fetch
    monkeypatch.setattr(iso_helpers, "MARK", "vazou-do-pai")
    cfg = Config(raindrop_token="SEGREDO-R", claude_oauth_token="SEGREDO-C", groq_api_key="SEGREDO-G", apify_token="SEGREDO-A")
    r = fetch({"id": 1, "link": "https://blog.com/a", "type": "article"}, tmp_path, cfg, {"jina": "chave-do-jina"}, None,
              fns={"article": iso_helpers.probe_article}, isolate=True)
    seen = json.loads(r.text)
    assert seen["mark"] == "valor-de-fabrica"                          # fork levaria a memória do pai
    assert seen["raindrop_token"] == "" and seen["claude_oauth_token"] == "" and seen["groq_api_key"] == ""
    assert seen["apify_token"] is None
    assert seen["jk"] == "chave-do-jina"                               # a chave daquele fetcher continua indo


# ---------- SCC-13: rclone.conf do container só com o remote usado ----------
def test_scc13_rclone_conf_keeps_only_the_dropbox_remote(tmp_path):
    helper = ROOT / "host/rclone_only_remote.py"
    src = tmp_path / "root.conf"
    src.write_text("[gdrive]\ntype = drive\ntoken = {\"a\":1}\n\n[dropbox]\ntype = dropbox\ntoken = {\"access_token\":\"x\"}\n\n[s3]\ntype = s3\n")
    dst = tmp_path / "rclone" / "rclone.conf"; dst.parent.mkdir()
    subprocess.run([sys.executable, str(helper), str(src), str(dst), "dropbox"], check=True, capture_output=True)
    assert dst.read_text() == "[dropbox]\ntype = dropbox\ntoken = {\"access_token\":\"x\"}\n"
    assert dst.stat().st_mode & 0o777 == 0o600
    # arquivo já existente (token renovado pelo container) vale mais que o do root: só perde os outros remotes
    dst.write_text("[dropbox]\ntype = dropbox\ntoken = {\"access_token\":\"novo\"}\n[gdrive]\ntype = drive\n")
    subprocess.run([sys.executable, str(helper), str(src), str(dst), "dropbox"], check=True, capture_output=True)
    assert dst.read_text() == "[dropbox]\ntype = dropbox\ntoken = {\"access_token\":\"novo\"}\n"


def test_scc13_deploy_no_longer_copies_the_whole_root_conf():
    deploy = (ROOT / "deploy.sh").read_text()
    assert "cp /root/.config/rclone/rclone.conf" not in deploy
    assert "rclone_only_remote.py" in deploy


# ---------- SCC-14: título do item entra limpo no log da wiki ----------
def test_scc14_title_is_sanitized_in_the_log_line():
    items = {1: {**ITEMS[1], "title": "Oi ![x](https://evil.example/p.png) <img src=x> [[CLAUDE]] `code` | col\nlinha2"}}
    m = plan_ops(OUT, Snap({"wiki/energia-e-recarga.md": (PAGE, "rev1")}), items, Config(), "run1", "2026-09-11", VOCAB)
    log = [o for o in m.ops if o.path == "wiki/log/2026-09_curated.md"][0].after.decode()
    line = [l for l in log.splitlines() if " r1 " in l][0]
    for bad in ("![", "](", "<", ">", "[[CLAUDE", "`", "| col", "https://evil"):
        assert bad not in line.split(" → ")[0], (bad, line)
    assert "linha2" in line and "\n" not in line


# ---------- SCC-15: list_all não corta 2000 itens em silêncio ----------
def test_scc15_list_all_unbounded_and_warns_when_capped(monkeypatch, capsys):
    from lib.raindrop import RaindropClient
    monkeypatch.setattr("lib.raindrop.time.sleep", lambda s: None)

    def http(method, url, body=None):
        page = int(url.rsplit("&page=", 1)[1])
        items = [] if page >= 45 else [{"_id": page * 50 + i, "created": "2026-01-01"} for i in range(50)]
        return 200, {"items": items}
    c = RaindropClient("t", http=http)
    assert len(c.list_all(max_pages=None)) == 45 * 50
    assert len(c.list_all()) == 40 * 50
    assert "teto" in capsys.readouterr().err


def test_scc15_backfill_lists_without_cap(tmp_path, monkeypatch):
    cli, app, store = _app(tmp_path, monkeypatch)
    seen = {}
    app.raindrop.list_all = lambda since_iso=None, **k: seen.update(k) or []
    app.backfill(None, dry=True, cont=False)
    assert "max_pages" in seen and seen["max_pages"] is None


# ---------- SCC-17: retry sem id não derruba o ciclo nem perde pedidos ----------
def test_scc17_cli_rejects_retry_without_numeric_id(tmp_path, monkeypatch):
    cli, app, store = _app(tmp_path, monkeypatch)
    env = {**ENV, "WORK_DIR": str(tmp_path), "HEALTH_PUBLIC_DIR": str(tmp_path / "public")}
    for argv in (["retry"], ["retry", "abc"], ["recapture"], ["undo"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv + ["--config", str(ROOT / "config.json")], env=env)
        assert e.value.code == 2


def test_scc17_bad_request_does_not_drop_the_others(tmp_path, monkeypatch):
    cli, app, store = _app(tmp_path, monkeypatch)
    done = []
    monkeypatch.setattr(app, "recapture", lambda iid: done.append(iid))
    app.journal.enqueue_request("retry", None)            # entrou pela fila sem passar pelo CLI
    app.journal.enqueue_request("recapture", "42")
    app.cycle()
    assert done == [42]
    assert app.journal.pop_requests() == []


# ---------- SCC-20: a fase de captura dá batimento ----------
def test_scc20_capture_phase_beats(tmp_path, monkeypatch):
    items = [_item(11), _item(12)]
    cli, app, store = _app(tmp_path, monkeypatch, items=items)
    beats = []
    monkeypatch.setattr(app, "beat", lambda recs: beats.append(1))
    out = app.cycle()
    assert out["captured"] == 2 and len(beats) >= 2


# ---------- SCC-21: deploy não leva lixo do Mac para a VPS ----------
def test_scc21_deploy_excludes_local_leftovers(tmp_path):
    if not shutil.which("rsync"):
        pytest.skip("rsync ausente")
    src = tmp_path / "src"; dst = tmp_path / "dst"
    for rel in (".venv 2/bin/python", ".venv.parcial-1310/x", ".venv/x", ".git/HEAD", ".git.parcial-1310/HEAD",
                ".DS_Store", "lib/.DS_Store", ".env", "runs/a.json", "lib/applier.py", ".env.example", "PLAN.md"):
        p = src / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    subprocess.run(["rsync", "-a", f"--exclude-from={ROOT / 'deploy.exclude'}", f"{src}/", f"{dst}/"], check=True)
    got = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
    assert got == [".env.example", "lib/applier.py"]
    deploy = (ROOT / "deploy.sh").read_text()
    assert "--exclude-from=" in deploy and "deploy.exclude" in deploy and "--delete-excluded" in deploy
    di = (ROOT / ".dockerignore").read_text().split()
    for pat in ("**/.venv*", "**/.git.*", "**/.DS_Store", "**/.env", "**/runs"):
        assert pat in di


# ---------- SCC-26: merge_tags não apaga tag que o usuário pôs no meio-tempo ----------
def test_scc26_merge_tags_rereads_before_writing():
    from lib.raindrop import RaindropClient
    reads = [["dev"], ["dev", "do-usuario"], ["dev", "do-usuario"]]
    puts = []

    def http(method, url, body=None):
        if method == "GET":
            tags = reads.pop(0) if reads else ["dev", "do-usuario"]
            return 200, {"item": {"_id": 1, "tags": tags, "note": ""}}
        puts.append(body)
        return 200, {"item": {"_id": 1, **body}}
    RaindropClient("t", http=http).merge_tags(1, ["sr:wiki"], ["sr:raw"])
    assert puts == [{"tags": ["dev", "do-usuario", "sr:wiki"]}]


def test_scc11_secrets_leave_the_process_environment(tmp_path, monkeypatch):
    cli, app, store = _app(tmp_path, monkeypatch)
    for k, v in {**ENV, "APIFY_TOKEN": "a", "WORK_DIR": str(tmp_path), "HEALTH_PUBLIC_DIR": str(tmp_path / "public")}.items():
        monkeypatch.setenv(k, v)
    assert cli.main(["status", "--config", str(ROOT / "config.json")]) == 0
    assert not [k for k in (*ENV, "APIFY_TOKEN") if k in os.environ]
