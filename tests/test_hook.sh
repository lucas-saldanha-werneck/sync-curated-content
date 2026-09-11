#!/bin/bash
# Testa hooks/curated-suggest.sh isolado (HOME e SR_VAULT redirecionados p/ tmpdir).
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$DIR/hooks/curated-suggest.sh"
chmod +x "$HOOK"

TMP=$(mktemp -d)
export HOME="$TMP/home"
export SR_VAULT="$TMP/vault"
mkdir -p "$HOME/.claude" "$SR_VAULT/raw/curated/_state"

cat > "$SR_VAULT/raw/curated/_state/catalog-index.jsonl" <<'JSON'
{"id":1,"type":"skill","title":"Claude Video","triggers":["video","youtube","transcricao"]}
JSON

export CLAUDE_SESSION_ID="test-hook-$$"
SEEN="/tmp/curated-seen-${CLAUDE_SESSION_ID}"
rm -f "$SEEN"

pass=0; fail=0
check() {
  local desc="$1" ok="$2"
  if [ "$ok" = "1" ]; then echo "PASS: $desc"; pass=$((pass+1)); else echo "FAIL: $desc"; fail=$((fail+1)); fi
}

# 1: prompt com >=2 gatilhos -> exatamente 1 linha "Catálogo: r1 ..."
OUT1=$(printf '%s' '{"prompt": "quero transcrever um vídeo do youtube"}' | "$HOOK")
N1=$(printf '%s\n' "$OUT1" | grep -c '^Catálogo: r1')
TOT1=$(printf '%s\n' "$OUT1" | grep -c '.')
[ "$N1" = "1" ] && [ "$TOT1" = "1" ] && OK1=1 || OK1=0
check "prompt com gatilhos -> 1 linha Catálogo: r1" "$OK1"

# 2: mesmo prompt, mesma sessão -> vazio (id já visto)
OUT2=$(printf '%s' '{"prompt": "quero transcrever um vídeo do youtube"}' | "$HOOK")
[ -z "$OUT2" ] && OK2=1 || OK2=0
check "repetição na mesma sessão -> vazio" "$OK2"

# 3: prompt curto -> vazio
OUT3=$(printf '%s' '{"prompt": "oi"}' | "$HOOK")
[ -z "$OUT3" ] && OK3=1 || OK3=0
check "prompt curto -> vazio" "$OK3"

# 4: --status com status.md contendo DEGRADADO -> 1 linha
echo "Status: DEGRADADO" > "$SR_VAULT/raw/curated/_state/status.md"
OUT4=$("$HOOK" --status)
N4=$(printf '%s\n' "$OUT4" | grep -c 'DEGRADADO')
TOT4=$(printf '%s\n' "$OUT4" | grep -c '.')
[ "$N4" = "1" ] && [ "$TOT4" = "1" ] && OK4=1 || OK4=0
check "--status com DEGRADADO -> 1 linha" "$OK4"

rm -f "$SEEN"
rm -rf "$TMP"

echo "----"
echo "$pass PASS, $fail FAIL"
[ "$fail" -eq 0 ]
