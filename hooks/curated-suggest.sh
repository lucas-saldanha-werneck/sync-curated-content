#!/bin/zsh
# UserPromptSubmit: casa gatilhos do catálogo com o prompt; SessionStart (--status): avisa saúde/backlog.
# Lê espelho local ~/.claude/curated-catalog.jsonl (copiado do vault se mais novo; timeout 2 s por causa do Dropbox dataless).
set -u
VAULT="${SR_VAULT:-$HOME/Library/CloudStorage/Dropbox/Projects/vault}"
SRC="$VAULT/raw/curated/_state/catalog-index.jsonl"; MIR="$HOME/.claude/curated-catalog.jsonl"; LOG="$HOME/.claude/curated-suggest.log"
SEEN="/tmp/curated-seen-${CLAUDE_SESSION_ID:-$PPID}"
# `timeout` não vem no macOS (só com coreutils do Homebrew, às vezes como gtimeout): sem ele, roda sem limite
if command -v timeout >/dev/null 2>&1; then t2() { timeout 2 "$@"; }
elif command -v gtimeout >/dev/null 2>&1; then t2() { gtimeout 2 "$@"; }
else t2() { "$@"; }; fi
refresh() { [ -f "$SRC" ] && { [ ! -f "$MIR" ] || [ "$SRC" -nt "$MIR" ]; } && t2 cp "$SRC" "$MIR" 2>/dev/null; }
if [ "${1:-}" = "--status" ]; then
  refresh; ST="$VAULT/raw/curated/_state/status.md"
  [ -f "$ST" ] && t2 grep -q 'DEGRADADO' "$ST" 2>/dev/null && echo "sync-raindrop: status DEGRADADO — ver raw/curated/_state/status.md"
  exit 0
fi
INPUT=$(cat); PROMPT=$(printf '%s' "$INPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)
[ ${#PROMPT} -lt 20 ] && exit 0
refresh; [ -f "$MIR" ] || exit 0
python3 - "$MIR" "$SEEN" "$LOG" <<'EOF' "$PROMPT"
import json, re, sys, unicodedata, time
mir, seen_path, log, prompt = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
def norm(s): return re.sub(r"[^a-z0-9 -]", " ", unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode())
p = " " + " ".join(norm(prompt).split()) + " "
try: seen = set(open(seen_path).read().split())
except FileNotFoundError: seen = set()
best = None
for line in open(mir, encoding="utf-8"):
    try: e = json.loads(line)
    except Exception: continue
    if str(e.get("id")) in seen: continue
    trig = e.get("triggers") if isinstance(e, dict) else None
    if not isinstance(trig, list): continue          # item sem catálogo (skip) tem triggers null: pula a linha, não o hook
    hits = [t for t in trig if isinstance(t, str) and f" {norm(t).strip()} " in p]
    if len(hits) >= 2 and (best is None or len(hits) > best[0]): best = (len(hits), e)
if best:
    e = best[1]; title = re.sub(r"[^A-Za-z0-9 ,.:/_-]", "", str(e.get("title", "")))[:60]
    print(f"Catálogo: r{e['id']} · {e.get('type','')} · {title} · wiki/ferramentas-e-ideias.md#r{e['id']}")
    open(seen_path, "a").write(f"{e['id']}\n"); open(log, "a").write(f"{time.strftime('%F %T')} r{e['id']} hits={best[0]}\n")
EOF
exit 0
