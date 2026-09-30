"""Saúde: checks reais antes de qualquer ping; status.md legível; prova de auth do Claude."""
from __future__ import annotations
import json, os, subprocess, urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from lib.records import terminal_state, has_capture
from lib.distill import _preexec_as_app_claude

@dataclass
class Health:
    ok: bool; checks: dict; ts: str

ABORT_STREAK = 3     # lotes seguidos abortados por exceção = serviço parado com loop vivo (2026-09-30: wiki > MAX_FILES)

def proxy_probe(proxy_url: str, open_with=None, url: str = "https://www.youtube.com/generate_204") -> tuple[bool, str]:
    """Sai pelo proxy residencial (YTDLP_PROXY) e espera resposta. Pega túnel sem rota (regra do
    wg-quick apagada por restart do networkd em 2026-09-29: proxy escutando, toda conexão em timeout)."""
    if open_with is None:
        open_with = lambda p: urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p})).open
    try:
        with open_with(proxy_url)(urllib.request.Request(url, method="HEAD"), timeout=20) as r:
            return True, f"ok ({r.status})"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]

def run_checks(journal, records_map, cfg, now, last_poll_ts, disk_free_pct, auth_ok_ts, work_dir,
               heartbeat_ts=None, proxy=None) -> "Health":
    c = {}
    # "vivo" = o loop deu sinal, não necessariamente um poll novo: em backfill sustentado o ciclo passa
    # horas dentro dos lotes sem voltar a ler o Raindrop, e o monitor alarmava à toa (2026-09-12).
    vivo_ts = max([t for t in (last_poll_ts, heartbeat_ts) if t is not None], default=None)
    c["loop_vivo"] = (vivo_ts is not None and (now - vivo_ts) <= timedelta(minutes=2), f"último batimento {vivo_ts}")
    c["auth"] = (auth_ok_ts is not None and (now - auth_ok_ts) <= timedelta(hours=24), f"auth ok em {auth_ok_ts}")
    old = []
    for st in ("captured", "retry"):
        for it in journal.items_in_state(st):
            try: updated_at = datetime.fromisoformat(it["updated_at"])
            except Exception: continue
            if updated_at.tzinfo is None: updated_at = updated_at.replace(tzinfo=now.tzinfo)
            if now - updated_at > timedelta(hours=24) and terminal_state(records_map.get(it["id"], set())) is None: old.append(it["id"])
    c["pendencia_velha"] = (not old, f"{len(old)} itens > 24 h: {old[:5]}")
    runs = [dict(r) for r in journal.con.execute("SELECT * FROM runs WHERE kind='distill' ORDER BY started")]
    bad_open = False
    for r in runs:
        if r.get("status") in ("distill_conflict", "distill_invalid"):
            try: t = datetime.fromisoformat(r["started"])
            except Exception: continue
            if now - t <= timedelta(hours=6): bad_open = True
        elif r.get("status") == "ok": bad_open = False
    c["conflitos"] = (not bad_open, "lote com conflito/inválido sem sucesso posterior" if bad_open else "ok")
    done = [r for r in runs if r.get("status") != "running"][-ABORT_STREAK:]
    aborting = len(done) == ABORT_STREAK and all(r.get("status") == "aborted" for r in done)
    last_err = ""
    if aborting:
        try: last_err = (json.loads(done[-1].get("summary") or "{}").get("error") or "")[:160]
        except Exception: pass
    c["lotes_abortando"] = (not aborting, f"últimos {ABORT_STREAK} lotes abortados: {last_err}" if aborting else "ok")
    if proxy is not None: c["proxy"] = proxy
    c["disco"] = (disk_free_pct > 20, f"{disk_free_pct:.0f}% livre")
    pend = sum(1 for k, v in records_map.items() if terminal_state(v) is None)
    c["fila"] = (pend <= cfg.queue_max, f"{pend} pendentes")
    return Health(ok=all(v[0] for v in c.values()), checks=c, ts=now.isoformat(timespec="seconds"))

def write_health_files(h: Health, public_dir: Path, work_dir: Path) -> None:
    Path(work_dir).mkdir(parents=True, exist_ok=True); Path(public_dir).mkdir(parents=True, exist_ok=True)
    (Path(work_dir) / "health.json").write_text(json.dumps({"ok": h.ok, "ts": h.ts, "checks": {k: {"ok": v[0], "info": v[1]} for k, v in h.checks.items()}}, ensure_ascii=False, indent=1))
    tmp = Path(public_dir) / "health.json.tmp"; tmp.write_text(json.dumps({"ok": h.ok, "ts": h.ts})); os.replace(tmp, Path(public_dir) / "health.json")

def ping_uptimerobot(url, h: Health, opener=urllib.request.urlopen) -> bool:
    if not url or not h.ok: return False
    try: opener(url, timeout=10); return True
    except Exception: return False

def status_md(journal, records_map, cfg, h: Health, groq_used: int) -> str:
    counts = {}
    for k, v in records_map.items():
        st = terminal_state(v) or ("captured" if has_capture(v) else "pendente"); counts[st] = counts.get(st, 0) + 1
    lines = [f"# sync-raindrop — status {h.ts}", "", f"Saúde: {'OK' if h.ok else 'DEGRADADO'}", ""]
    lines += ["| estado | itens |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", f"Lotes hoje: {journal.runs_today('distill', cfg.tz)} / {cfg.max_batches_per_day}", f"Groq hoje: {groq_used} s / {cfg.groq_daily_seconds} s", ""]
    lines += ["## Checks", ""] + [f"- {'✅' if v[0] else '❌'} {k}: {v[1]}" for k, v in h.checks.items()]
    errs = [dict(r) for r in journal.con.execute("SELECT id,last_error,updated_at FROM items WHERE last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 10")]
    if errs: lines += ["", "## Últimos erros", ""] + [f"- r{e['id']} ({e['updated_at']}): {str(e['last_error'])[:160]}" for e in errs]
    return "\n".join(lines) + "\n"

def claude_auth_probe(cfg, token: str, runner=subprocess.run) -> tuple[bool, str]:
    env = {"CLAUDE_CODE_OAUTH_TOKEN": token, "CLAUDE_CONFIG_DIR": "/tmp/cc-probe", "HOME": "/tmp", "PATH": os.environ.get("PATH", ""), "TZ": cfg.tz}
    try:
        p = runner(["claude", "-p", "Responda só: ok", "--model", cfg.claude_model, "--max-turns", "1", "--output-format", "text"], env=env, capture_output=True, text=True, timeout=120, preexec_fn=_preexec_as_app_claude())
    except Exception as e:
        return False, str(e)
    return (p.returncode == 0 and "ok" in (p.stdout or "").lower()), (p.stderr or p.stdout)[-300:]
