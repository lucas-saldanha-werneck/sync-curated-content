import json
from pathlib import Path
from datetime import datetime, timedelta
from lib.batch import run_batch, should_run, BatchDeps
from lib.config import Config
from lib.journal import Journal
from lib.records import Records
from lib.snapshot import Snapshot, UndoRepo
from tests.test_dropbox_store import make as make_store
from tests.test_capture import FakeRD

FIX = Path(__file__).parent / "fixtures"
OUT = json.loads((FIX / "claude_output.json").read_text())
PAGE = b"# Energia\n\n## Tarifa\n\ntexto antigo\n"


def make_deps(tmp_path, out=OUT):
    store = make_store(); cfg = Config()
    store.add("/Projects/vault/wiki/energia-e-recarga.md", PAGE); store.add("/Projects/vault/CLAUDE.md", b"regras")
    raw_rel = "raw/curated/2026-09-10_youtube-fonte-a-r1.md"
    store.add(cfg.vault_path(raw_rel), b"---\nraindrop_id: 1\n---\n# Fonte A\nconteudo")
    rec = Records(store, cfg)
    rec.write(1, "meta", {"link": "https://fonte.com/a", "title": "Fonte A"}); rec.write(1, "captured", {"raw": raw_rel, "frames": [], "method": "captions", "platform": "youtube"}); rec.refresh()
    rd = FakeRD({"id": 1, "tags": ["sr:raw"], "link": "https://fonte.com/a", "title": "Fonte A"})
    deps = BatchDeps(cfg=cfg, store=store, records=rec, raindrop=rd, journal=Journal(tmp_path / "j.db"), snapshot=Snapshot(store, cfg),
                     undo_repo=UndoRepo(tmp_path / "git"), run_claude=lambda ws, cfg, tok, prompt, schema, **k: out, token="t",
                     prompt_template="P {{PAGES}} {{ITEMS}} {{TRIGGERS}} {{TYPES}}", schema_path=FIX / "claude_output.json", triggers_vocab={"inversor", "solar"})
    return deps, store, rd


def test_should_run_rules():
    cfg = Config(); now = datetime(2026, 9, 11, 12, 0)
    assert should_run([1, 2, 3, 4, 5], now, now, cfg, 0)
    assert not should_run([1], now - timedelta(minutes=5), now, cfg, 0)
    assert should_run([1], now - timedelta(minutes=16), now, cfg, 0)
    assert not should_run([1] * 5, now, now, cfg, 12)                       # teto diário
    assert should_run([1] * 5, now, datetime(2026, 9, 11, 3, 10), cfg, 12)  # janela noturna


def test_run_batch_happy_path(tmp_path):
    deps, store, rd = make_deps(tmp_path)
    res = run_batch([1], deps, tmp_path / "work")
    assert res["items"][1] == "wiki" and res["ops"] >= 3
    deps.records.refresh(); assert deps.records.state(1) == "wiki"
    page, _ = store.download("/Projects/vault/wiki/energia-e-recarga.md"); assert b"curated:r1" in page
    assert store.exists("/Projects/vault/wiki/ferramentas-e-ideias.md")[0] and store.exists("/Projects/vault/wiki/log/2026-09_curated.md")[0]
    assert rd.puts[-1][0] == ["sr:wiki"] and "sr:raw" in rd.puts[-1][1]
    assert deps.journal.runs_today("distill", "America/Sao_Paulo") == 1


def test_run_batch_invalid_output_keeps_items(tmp_path):
    from lib.distill import DistillError
    deps, store, rd = make_deps(tmp_path)
    def boom(*a, **k): raise DistillError("x")
    deps.run_claude = boom
    res = run_batch([1], deps, tmp_path / "work")
    assert res["status"] == "distill_invalid" and deps.records.state(1) is None


def test_run_batch_nowiki_revalidated(tmp_path):
    deps, store, rd = make_deps(tmp_path); rd.item["tags"] = ["nowiki"]
    res = run_batch([1], deps, tmp_path / "work")
    assert res["items"][1] == "nowiki"; deps.records.refresh(); assert deps.records.state(1) == "nowiki"


def test_run_batch_dry_tags_skips_merge_tags(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_DRY_TAGS", "1")
    deps, store, rd = make_deps(tmp_path)
    res = run_batch([1], deps, tmp_path / "work")
    assert res["items"][1] == "wiki" and rd.puts == []
