import json
from pathlib import Path
from lib.distill import build_workspace, claude_cmd, run_claude, render_prompt, DistillError
from lib.snapshot import Snap
from lib.config import Config
FIX = Path(__file__).parent / "fixtures"

def test_build_workspace_layout(tmp_path):
    snap = Snap({"wiki/a.md": (b"# A\n## Tarifa\n", "r1"), "CLAUDE.md": (b"regras", "r0")})
    ws = build_workspace(tmp_path / "ws", snap, {"raw/curated/x-r1.md": b"---\nraindrop_id: 1\n---\ntexto"}, {"raw/curated/x-r1": [("01.jpg", b"jpg")]})
    assert (ws / "wiki/a.md").read_bytes() == b"# A\n## Tarifa\n" and (ws / "CLAUDE.md").exists()
    assert (ws / "raw/curated/x-r1.md").exists() and (ws / "raw/curated/x-r1/frames/01.jpg").exists()
    deny = json.loads((ws / ".claude/settings.json").read_text())["permissions"]["deny"]
    assert any("Read(~/**)" in d for d in deny)
    assert "Read(/proc/**)" in deny and "Read(/secrets/**)" in deny
    assert "Read(/**)" not in deny and "Read(/var/**)" not in deny and "Read(/tmp/**)" not in deny
    assert "Read(//**)" not in deny

def test_build_workspace_permissions(tmp_path):
    snap = Snap({"CLAUDE.md": (b"regras", "r0")})
    ws = build_workspace(tmp_path / "ws2", snap, {}, {})
    mode = ws.stat().st_mode & 0o777
    assert mode == 0o700

def test_claude_cmd_flags():
    cmd = claude_cmd(Config(), Path("schema/distill.json"), "oi")
    assert cmd[0] == "claude" and cmd[1] == "-p" and cmd[2] == "oi"
    s = " ".join(cmd)
    assert "--permission-mode dontAsk" in s and "--output-format json" in s and "--json-schema" in s and "--model claude-fable-5-1" in s
    assert "--disallowedTools" in s and "Write" in s and "--bare" not in s

def test_run_claude_parses_structured_output(tmp_path):
    out = json.dumps({"type": "result", "structured_output": json.loads((FIX / "claude_output.json").read_text())})
    runner = lambda cmd, **k: type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    res = run_claude(tmp_path, Config(), "tok", "prompt", FIX / "claude_output.json", runner=runner)
    assert res["items"][0]["decision"] == "wiki"

def test_run_claude_falls_back_to_result_json_string(tmp_path):
    inner = json.loads((FIX / "claude_output.json").read_text())
    out = json.dumps({"type": "result", "result": json.dumps(inner, ensure_ascii=False)})
    runner = lambda cmd, **k: type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    res = run_claude(tmp_path, Config(), "tok", "prompt", FIX / "claude_output.json", runner=runner)
    assert res["items"][0]["decision"] == "wiki"

def test_run_claude_env_minimal(tmp_path):
    seen = {}
    def runner(cmd, **k): seen.update(k.get("env", {})); return type("P", (), {"returncode": 0, "stdout": '{"structured_output": {"items": []}}', "stderr": ""})()
    run_claude(tmp_path, Config(), "tok", "p", Path("schema/distill.json"), runner=runner)
    assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "tok" and set(seen) <= {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "HOME", "PATH", "TZ"}

def test_run_claude_passes_preexec_fn(tmp_path):
    seen = {}
    def runner(cmd, **k): seen.update(k); return type("P", (), {"returncode": 0, "stdout": '{"structured_output": {"items": []}}', "stderr": ""})()
    run_claude(tmp_path, Config(), "tok", "p", Path("schema/distill.json"), runner=runner)
    assert callable(seen.get("preexec_fn"))

def test_run_claude_raises_on_nonzero_rc(tmp_path):
    runner = lambda cmd, **k: type("P", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
    try:
        run_claude(tmp_path, Config(), "tok", "p", Path("schema/distill.json"), runner=runner)
        assert False, "deveria ter levantado DistillError"
    except DistillError as e:
        assert "boom" in str(e)

def test_render_prompt_substitutes():
    p = render_prompt("X {{PAGES}} Y {{TRIGGERS}} Z {{TYPES}} W {{ITEMS}}", [{"raindrop_id": 1, "raw": "raw/curated/a.md", "note": "n"}], ["wiki/a.md"], ["solar"], ["skill"])
    assert "wiki/a.md" in p and "solar" in p and "skill" in p and '"raindrop_id": 1' in p


def test_build_workspace_readable_by_app_claude_group(tmp_path, monkeypatch):
    """Bug 2026-09-11: chown p/ app-claude falhava calado (app sem CAP_CHOWN) e o ws ficava 700 do app →
    claude (uid app-claude) não lia nada e destilava "fonte inacessível". Fix: grupo app-claude + modos."""
    import os, stat
    from lib import distill
    gid = os.getgid()
    monkeypatch.setattr(distill, "_app_claude_pw", lambda: type("PW", (), {"pw_uid": 99999, "pw_gid": gid})())
    snap = Snap({"wiki/a.md": (b"# A\n", "r1"), "CLAUDE.md": (b"regras", "r0")})
    ws = build_workspace(tmp_path / "ws3", snap, {"raw/curated/x-r1.md": b"texto"}, {"raw/curated/x-r1": [("01.jpg", b"jpg")]})
    assert ws.stat().st_gid == gid and (ws / "raw/curated/x-r1.md").stat().st_gid == gid
    assert (ws / "raw/curated/x-r1/frames/01.jpg").stat().st_gid == gid
    assert ws.stat().st_mode & 0o777 == 0o750                       # grupo entra, outros não
    assert (ws / "raw/curated").stat().st_mode & 0o777 == 0o750
    assert (ws / "raw/curated/x-r1.md").stat().st_mode & 0o777 == 0o640
    assert (ws / ".claude/settings.json").stat().st_mode & 0o777 == 0o640
    for d in (".cc", ".home"):                                       # claude escreve config/sessão aqui
        assert (ws / d).stat().st_mode & 0o777 == 0o770
        assert (ws / d).stat().st_mode & stat.S_ISGID                # filhos herdam o grupo → app limpa depois


def test_build_workspace_chgrp_failure_is_loud(tmp_path, monkeypatch):
    import os
    from lib import distill
    monkeypatch.setattr(distill, "_app_claude_pw", lambda: type("PW", (), {"pw_uid": 99999, "pw_gid": 99999})())
    monkeypatch.setattr(os, "chown", lambda *a, **k: (_ for _ in ()).throw(PermissionError("no")))
    snap = Snap({"CLAUDE.md": (b"regras", "r0")})
    import pytest
    with pytest.raises(RuntimeError, match="app-claude"):
        build_workspace(tmp_path / "ws4", snap, {}, {})


def test_preexec_sets_group_friendly_umask():
    import os
    from lib.distill import _preexec_as_app_claude
    old = os.umask(0o022)
    try:
        _preexec_as_app_claude()()          # app-claude não existe no Mac → drop_to é no-op
        assert os.umask(0o022) == 0o007
    finally:
        os.umask(old)


def test_build_workspace_pretrusts_the_workspace(tmp_path):
    """Bug 2026-09-12: o claude passou a ABORTAR (rc=1) em workspace não confiado — 85 lotes seguidos perdidos.
    A própria mensagem do CLI manda pré-gravar hasTrustDialogAccepted no CLAUDE_CONFIG_DIR."""
    snap = Snap({"CLAUDE.md": (b"regras", "r0")})
    ws = build_workspace(tmp_path / "ws5", snap, {}, {})
    cfgj = json.loads((ws / ".cc/.claude.json").read_text())
    assert cfgj["projects"][str(ws)]["hasTrustDialogAccepted"] is True
    assert (ws / ".cc/.claude.json").stat().st_mode & 0o200            # o claude reescreve esse arquivo


def test_claude_credito_used_with_console_key(tmp_path, monkeypatch):
    import lib.distill as d
    monkeypatch.setattr(d.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    cfg = Config(claude_credit_api_key="console-key-for-test")
    assert claude_cmd(cfg, Path("schema/distill.json"), "oi")[0] == "claude-credito"
    seen = {}
    def runner(cmd, **k): seen.update(k.get("env", {})); return type("P", (), {"returncode": 0, "stdout": '{"structured_output": {"items": []}}', "stderr": ""})()
    run_claude(tmp_path, cfg, "tok", "p", Path("schema/distill.json"), runner=runner)
    assert seen["CLAUDE_CREDITO_KEY"] == "console-key-for-test" and seen["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert seen["CLAUDE_CREDITO_REAL"] == "/usr/bin/claude" and seen["CLAUDE_CREDITO_KEYCHAIN"] == "off"


def test_plain_claude_when_credito_missing(tmp_path, monkeypatch):
    import lib.distill as d
    monkeypatch.setattr(d.shutil, "which", lambda name, path=None: None)
    cfg = Config(claude_credit_api_key="console-key-for-test")
    assert claude_cmd(cfg, Path("schema/distill.json"), "oi")[0] == "claude"
