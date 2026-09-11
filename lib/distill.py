"""Roda o claude -p em modo só-leitura sobre um workspace descartável e devolve o JSON validado pelo CLI."""
from __future__ import annotations
import json, os, pwd, shutil, subprocess
from pathlib import Path
from lib.snapshot import Snap


class DistillError(Exception): ...


# Read(/**) e Read(//**) NÃO entram: "//" normaliza para "/", então qualquer um dos dois
# negaria o filesystem inteiro — workspace incluso (ele vive sob um path absoluto,
# cfg.work_dir/distill/<run_id>). Read(/var/**) e Read(/tmp/**) também não: o workspace real
# fica em /var/lib/sync-raindrop/distill/<run_id> — deny bate allow no Claude Code, então uma
# regra genérica de /var/** bloquearia o Claude de ler o próprio workspace que ele deveria ler.
# Em vez disso, nega hosts/paths específicos fora do workspace.
DENY = ["Read(~/**)", "Read(/proc/**)", "Read(/etc/**)", "Read(/run/**)", "Read(/secrets/**)",
        "Read(/root/**)", "Read(/home/**)", "Read(/opt/**)", "Read(/public/**)",
        "Read(/var/lib/sync-raindrop/journal.db*)", "Read(/var/lib/sync-raindrop/wiki-git/**)",
        "Read(/var/lib/sync-raindrop/runs/**)", "Read(/var/lib/sync-raindrop/capture/**)",
        "Bash(*)", "Write(*)", "Edit(*)", "WebFetch(*)", "WebSearch(*)", "NotebookEdit(*)"]


def _app_claude_pw():
    """pwd.struct_passwd de app-claude neste host, ou None se o usuário não existir (Mac/dev/testes).

    Único ponto que chama pwd.getpwnam("app-claude") — tanto o chown de build_workspace quanto
    o preexec_fn de run_claude passam por aqui.
    """
    try:
        return pwd.getpwnam("app-claude")
    except KeyError:
        return None


def _app_claude_ids() -> tuple[int, int] | None:
    pw = _app_claude_pw()
    return (pw.pw_uid, pw.pw_gid) if pw else None


def _preexec_as_app_claude():
    """preexec_fn: troca o processo filho para o uid/gid de app-claude quando ele existir.

    A checagem acontece dentro do callable (não na hora de montá-lo) porque o preexec_fn
    só é de fato executado no filho, após o fork — em Mac/testes ele nunca roda de verdade
    (o runner é injetado), então a ausência de app-claude nunca derruba os testes.
    """
    def _switch():
        pw = _app_claude_pw()
        if pw is None:
            return
        try:
            os.setgid(pw.pw_gid)
            os.setuid(pw.pw_uid)
        except PermissionError:
            pass
    return _switch


def build_workspace(ws: Path, snap: Snap, raws: dict[str, bytes], frames: dict[str, list]) -> Path:
    ws = Path(ws)
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    for rel, (data, _rev) in snap.files.items():
        p = ws / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    for rel, data in raws.items():
        p = ws / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    for reldir, lst in frames.items():
        d = ws / reldir / "frames"; d.mkdir(parents=True, exist_ok=True)
        for name, blob in lst:
            (d / name).write_bytes(blob)
    (ws / ".claude").mkdir(exist_ok=True)
    (ws / ".claude/settings.json").write_text(json.dumps({"permissions": {"allow": ["Read(./**)", "Glob(*)", "Grep(*)"], "deny": DENY}}, indent=1))
    (ws / ".cc").mkdir(exist_ok=True)
    (ws / ".home").mkdir(exist_ok=True)

    os.chmod(ws, 0o700)
    ids = _app_claude_ids()
    if ids is not None:
        uid, gid = ids
        try:
            os.chown(ws, uid, gid)
        except PermissionError:
            pass
        for root, dirs, files in os.walk(ws):
            for name in dirs + files:
                try:
                    os.chown(os.path.join(root, name), uid, gid)
                except PermissionError:
                    pass
    return ws


def claude_cmd(cfg, schema_path: Path, prompt: str) -> list[str]:
    # O prompt vem logo após -p, ANTES de --allowedTools/--disallowedTools: essas são flags
    # variádicas (commander.js, <tools...>) que continuam engolindo qualquer argumento seguinte
    # que não comece com "-" — se o prompt vier depois, ele some como "mais um tool name" e o
    # claude falha com "Input must be provided either through stdin or as a prompt argument"
    # (achado do spike da Task 0).
    return ["claude", "-p", prompt,
            "--model", cfg.claude_model,
            "--output-format", "json",
            "--json-schema", Path(schema_path).read_text(encoding="utf-8"),
            "--permission-mode", "dontAsk",
            "--allowedTools", "Read,Glob,Grep",
            "--disallowedTools", "Write,Edit,Bash,WebFetch,WebSearch,NotebookEdit",
            "--max-turns", "40"]


def run_claude(ws: Path, cfg, token: str, prompt: str, schema_path: Path, runner=subprocess.run) -> dict:
    ws = Path(ws)
    env = {"CLAUDE_CODE_OAUTH_TOKEN": token, "CLAUDE_CONFIG_DIR": str(ws / ".cc"), "HOME": str(ws / ".home"),
           "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "TZ": cfg.tz}
    try:
        p = runner(claude_cmd(cfg, schema_path, prompt), cwd=str(ws), env=env, capture_output=True, text=True,
                   timeout=cfg.claude_timeout_s, preexec_fn=_preexec_as_app_claude())
    except subprocess.TimeoutExpired as e:
        raise DistillError(f"claude timeout {cfg.claude_timeout_s}s") from e
    if p.returncode != 0:
        raise DistillError(f"claude rc={p.returncode}: {(p.stderr or p.stdout)[-800:]}")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise DistillError(f"saída não é JSON: {p.stdout[-300:]}") from e

    so = data.get("structured_output")
    if not isinstance(so, dict):
        # fallback: algumas saídas só trazem o JSON estruturado, já serializado, em "result"
        result = data.get("result")
        if isinstance(result, str):
            try:
                so = json.loads(result)
            except json.JSONDecodeError:
                so = None
    if not isinstance(so, dict) or "items" not in so:
        raise DistillError(f"sem structured_output: {str(data)[:300]}")
    return so


def render_prompt(template: str, items: list[dict], pages: list[str], triggers_vocab: list[str], types_vocab: list[str]) -> str:
    return (template.replace("{{PAGES}}", ", ".join(f"`{p}`" for p in pages))
                    .replace("{{TRIGGERS}}", ", ".join(triggers_vocab))
                    .replace("{{TYPES}}", ", ".join(types_vocab))
                    .replace("{{ITEMS}}", json.dumps(items, ensure_ascii=False, indent=1)))
