from datetime import datetime, timedelta
from pathlib import Path
import json
from lib.health import run_checks, write_health_files, ping_uptimerobot, status_md, claude_auth_probe
from lib.journal import Journal
from lib.config import Config

def base(tmp_path):
    j = Journal(tmp_path / "j.db"); now = datetime.now().astimezone()
    return j, now

def test_all_ok(tmp_path):
    j, now = base(tmp_path)
    h = run_checks(j, {}, Config(), now, now - timedelta(seconds=30), 60.0, now - timedelta(hours=1), tmp_path)
    assert h.ok and all(v[0] for v in h.checks.values())

def test_stale_poll_and_old_pending_fail(tmp_path):
    j, now = base(tmp_path)
    j.upsert_item(9, state="captured", created=(now - timedelta(days=2)).isoformat())
    j.con.execute("UPDATE items SET updated_at=? WHERE id=?", ((now - timedelta(days=2)).isoformat(), 9))
    j.con.commit()
    h = run_checks(j, {9: {"meta", "captured"}}, Config(), now, now - timedelta(minutes=5), 60.0, now, tmp_path)
    assert not h.ok and not h.checks["loop_vivo"][0] and not h.checks["pendencia_velha"][0]
    # Item with old created but fresh updated_at should NOT be flagged
    j.upsert_item(10, state="captured", created=(now - timedelta(days=2)).isoformat())
    h = run_checks(j, {9: {"meta", "captured"}, 10: {"meta", "captured"}}, Config(), now, now - timedelta(minutes=5), 60.0, now, tmp_path)
    assert not h.checks["pendencia_velha"][0] and "9" in h.checks["pendencia_velha"][1] and "10" not in h.checks["pendencia_velha"][1]

def test_conflict_run_fails_until_ok_after(tmp_path):
    j, now = base(tmp_path)
    r = j.start_run("distill", [1]); j.finish_run(r, "distill_conflict", {})
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path); assert not h.checks["conflitos"][0]
    r2 = j.start_run("distill", [1]); j.finish_run(r2, "ok", {})
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path); assert h.checks["conflitos"][0]

def test_aborted_runs_fail_until_ok_after(tmp_path):
    """Todo lote abortando (ex.: wiki > MAX_FILES em 2026-09-30) com health OK: 3 seguidos derrubam o health."""
    j, now = base(tmp_path)
    for _ in range(2):
        r = j.start_run("distill", [1]); j.finish_run(r, "aborted", {"error": "RuntimeError: wiki com 4685 arquivos .md > 300"})
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path); assert h.checks["lotes_abortando"][0]
    r = j.start_run("distill", [1]); j.finish_run(r, "aborted", {"error": "RuntimeError: wiki com 4685 arquivos .md > 300"})
    j.start_run("distill", [1])                                   # órfão "running" não conta nem zera
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path)
    assert not h.ok and not h.checks["lotes_abortando"][0] and "4685" in h.checks["lotes_abortando"][1]
    r = j.start_run("distill", [1]); j.finish_run(r, "ok", {})
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path); assert h.checks["lotes_abortando"][0]

def test_proxy_check(tmp_path):
    j, now = base(tmp_path)
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path); assert "proxy" not in h.checks   # sem proxy configurado
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path, proxy=(False, "timed out"))
    assert not h.ok and h.checks["proxy"] == (False, "timed out")
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path, proxy=(True, "ok")); assert h.ok

def test_proxy_probe():
    from lib.health import proxy_probe
    class Resp:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert proxy_probe("http://p:1", open_with=lambda proxy: (lambda req, timeout: Resp()))[0] is True
    def bad(req, timeout): raise OSError("timed out")
    ok, info = proxy_probe("http://p:1", open_with=lambda proxy: bad)
    assert ok is False and "timed out" in info

def test_files_and_ping(tmp_path):
    j, now = base(tmp_path)
    h = run_checks(j, {}, Config(), now, now, 60.0, now, tmp_path)
    write_health_files(h, tmp_path / "pub", tmp_path / "work")
    pub = json.loads((tmp_path / "pub/health.json").read_text()); assert pub["ok"] is True and set(pub) == {"ok", "ts"}
    pinged = []
    assert ping_uptimerobot("https://hb/x", h, opener=lambda u, timeout=10: pinged.append(u) or type("R", (), {"status": 200})()) and pinged
    h.ok = False; assert not ping_uptimerobot("https://hb/x", h, opener=lambda u, timeout=10: pinged.append(u)) and len(pinged) == 1
    md = status_md(j, {}, Config(), h, 120); assert "Groq hoje:" in md and "| estado |" in md.lower() or "estado" in md.lower()

def test_auth_probe_passes_preexec_closure():
    captured_kwargs = {}
    def fake_runner(cmd, **kw):
        captured_kwargs.update(kw)
        return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
    claude_auth_probe(Config(), "token", runner=fake_runner)
    assert callable(captured_kwargs["preexec_fn"]) and captured_kwargs["preexec_fn"].__name__ == "_switch"


def test_loop_vivo_uses_heartbeat_not_only_the_poll(tmp_path):
    """Durante backfill sustentado o ciclo fica horas dentro dos lotes e não faz poll novo.
    O loop está VIVO — o batimento tem que contar, senão o monitor alarma à toa (2026-09-12)."""
    j, now = base(tmp_path)
    h = run_checks(j, {}, Config(), now, now - timedelta(minutes=40), 60.0, now - timedelta(hours=1), tmp_path,
                   heartbeat_ts=now - timedelta(seconds=20))
    assert h.checks["loop_vivo"][0]
    parado = run_checks(j, {}, Config(), now, now - timedelta(minutes=40), 60.0, now - timedelta(hours=1), tmp_path,
                        heartbeat_ts=now - timedelta(minutes=40))
    assert not parado.checks["loop_vivo"][0]


def test_status_conta_item_esperando_recaptura_como_pendente(tmp_path):
    j, now = base(tmp_path)
    recs = {1: {"meta", "captured", "distill", "parcial", "recap1"}}
    h = run_checks(j, recs, Config(), now, now, 60.0, now, tmp_path, heartbeat_ts=now)
    md = status_md(j, recs, Config(), h, 0)
    assert "| pendente | 1 |" in md and "| captured |" not in md
