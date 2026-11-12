"""Roda o claude -p em modo só-leitura sobre um workspace descartável e devolve o JSON validado pelo CLI."""
from __future__ import annotations
import json, os, pwd, shutil, signal, subprocess
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


def _preexec_as_app_claude(alarm_s: int | None = None):
    """preexec_fn: troca o processo filho para o uid/gid de app-claude quando ele existir.

    A checagem acontece dentro do callable (não na hora de montá-lo) porque o preexec_fn
    só é de fato executado no filho, após o fork — em Mac/testes ele nunca roda de verdade
    (o runner é injetado), então a ausência de app-claude nunca derruba os testes.
    """
    def _switch():
        if alarm_s:
            # sem o `timeout` no PATH: o alarme sobrevive à troca de uid e ao exec, e o SIGALRM (ação padrão)
            # encerra o claude mesmo quando o pai (app, sem CAP_KILL) não consegue matá-lo
            signal.alarm(int(alarm_s))
        # falha alta: se app-claude existe (produção) e a troca falhar, o claude NÃO roda (review Opus M2)
        from lib.privs import drop_to
        drop_to("app-claude")
        os.umask(0o007)                  # arquivos/dirs que o claude criar (.cc/.home) ficam graváveis pelo grupo → app limpa
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
    # Workspace descartável e novo a cada lote: sem isso o claude ABORTA (rc=1, "this workspace has not been
    # trusted") e o lote inteiro se perde — 85 lotes seguidos em 2026-09-12. A própria mensagem do CLI manda
    # pré-gravar a chave aqui. Fica gravável pelo grupo: o claude reescreve esse arquivo no start.
    (ws / ".cc/.claude.json").write_text(json.dumps({"projects": {str(ws): {"hasTrustDialogAccepted": True}}}, indent=1))

    # O claude roda como app-claude (uid distinto). O orquestrador (app) NÃO tem CAP_CHOWN: `os.chown` p/ outro
    # uid falhava calado e o ws ficava 700 do app → claude não lia nada e destilava "fonte inacessível"
    # (bug 2026-09-11, todos os lotes após o sandbox de uid). Fix: app é membro do grupo app-claude (Dockerfile),
    # entrega o ws pelo GRUPO (chgrp não precisa de capability) com modos 750/640; .cc/.home 2770 (claude escreve
    # config/sessão; setgid + umask 007 no filho → o que ele criar fica do grupo e gravável → app limpa depois).
    pw = _app_claude_pw()
    if pw is None:                                   # Mac/testes: sem sandbox de uid, ws só do dono
        os.chmod(ws, 0o700)
        return ws
    gid = pw.pw_gid
    try:
        for root, dirs, files in os.walk(ws):
            for name in dirs:
                d = os.path.join(root, name); os.chown(d, -1, gid)
                os.chmod(d, 0o2770 if d in (str(ws / ".cc"), str(ws / ".home")) else 0o750)
            for name in files:
                f = os.path.join(root, name); os.chown(f, -1, gid)
                priv = root.startswith(str(ws / ".cc")) or root.startswith(str(ws / ".home"))
                os.chmod(f, 0o660 if priv else 0o640)      # o claude reescreve o que está em .cc/.home
        os.chown(ws, -1, gid); os.chmod(ws, 0o750)
    except PermissionError as e:
        raise RuntimeError(f"workspace não pôde ser entregue ao grupo app-claude (app é membro do grupo?): {e}") from e
    return ws


TIMEOUT_KILL_AFTER_S = 10
CREDITO_BIN = "claude-credito"
CREDITO_STATE = "/tmp/claude-credito"   # pausa de 6 h e registro; /tmp é o tmpfs gravável do container


def claude_bin(cfg) -> str:
    """claude-credito (crédito de API primeiro, assinatura se a API recusar) quando há chave do Console e o
    binário existe; senão o claude puro, na assinatura."""
    if getattr(cfg, "claude_credit_api_key", None) and shutil.which(CREDITO_BIN):
        return CREDITO_BIN
    return "claude"


def claude_cmd(cfg, schema_path: Path, prompt: str) -> list[str]:
    # O prompt vem logo após -p, ANTES de --allowedTools/--disallowedTools: essas são flags
    # variádicas (commander.js, <tools...>) que continuam engolindo qualquer argumento seguinte
    # que não comece com "-" — se o prompt vier depois, ele some como "mais um tool name" e o
    # claude falha com "Input must be provided either through stdin or as a prompt argument"
    # (achado do spike da Task 0).
    return [claude_bin(cfg), "-p", prompt,
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
    if claude_bin(cfg) == CREDITO_BIN:
        env.update(CLAUDE_CREDITO_KEY=cfg.claude_credit_api_key, CLAUDE_CREDITO_KEYCHAIN="off",
                   CLAUDE_CREDITO_REAL=shutil.which("claude", path=env["PATH"]) or "claude",
                   CLAUDE_CREDITO_STATE=CREDITO_STATE, CLAUDE_CREDITO_LOG=f"{CREDITO_STATE}/claude-credito.log")
    # O claude roda como app-claude e o pai (app, sem CAP_KILL) não consegue matá-lo: o timeout do
    # subprocess.run deixava o processo (e o claude real, neto do claude-credito) órfão. O `timeout` do coreutils
    # roda JÁ como app-claude, num grupo de processos próprio, e no prazo sinaliza o grupo inteiro (TERM, depois KILL).
    cmd = claude_cmd(cfg, schema_path, prompt)
    tbin = shutil.which("timeout", path=env["PATH"])
    if tbin:
        cmd = [tbin, f"--kill-after={TIMEOUT_KILL_AFTER_S}", f"{int(cfg.claude_timeout_s)}s", *cmd]
    backstop = cfg.claude_timeout_s + TIMEOUT_KILL_AFTER_S + 20       # só se o próprio `timeout` travar
    try:
        p = runner(cmd, cwd=str(ws), env=env, capture_output=True, text=True, timeout=backstop,
                   preexec_fn=_preexec_as_app_claude(None if tbin else cfg.claude_timeout_s))
    except (subprocess.TimeoutExpired, PermissionError) as e:        # PermissionError: kill() do pai levou EPERM
        raise DistillError(f"claude timeout {cfg.claude_timeout_s}s") from e
    if (tbin and p.returncode in (124, 137)) or p.returncode == -signal.SIGALRM:
        raise DistillError(f"claude timeout {cfg.claude_timeout_s}s (rc={p.returncode})")
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
