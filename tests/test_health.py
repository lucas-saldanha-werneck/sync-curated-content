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
