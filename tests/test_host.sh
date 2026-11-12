#!/bin/bash
# Testa os scripts de host/ com stubs (iptables, docker) e pastas temporárias: nada toca o sistema real.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d)
pass=0; fail=0
check() { if [ "$2" = "1" ]; then echo "PASS: $1"; pass=$((pass+1)); else echo "FAIL: $1"; fail=$((fail+1)); fi; }

# ---------- docker-user-egress.sh (SCC-22) ----------
STATE="$TMP/ipt.state"; : > "$STATE"
cat > "$TMP/iptables" <<'EOF'
#!/bin/bash
# stub mínimo: guarda "chain regra" num arquivo; -C/-I/-D/-S
S="$IPT_STATE"; op="$1"; chain="$2"; shift 2
case "$op" in
  -I) shift; { echo "$chain $*"; cat "$S"; } > "$S.n" && mv "$S.n" "$S" ;;   # posição 1
  -A) echo "$chain $*" >> "$S" ;;
  -C) grep -qxF -- "$chain $*" "$S" ;;
  -D) awk -v r="$chain $*" 'BEGIN{d=0} { if (!d && $0==r) { d=1; next } print }' "$S" > "$S.n" && mv "$S.n" "$S" ;;
  -S) grep "^$chain " "$S" | sed "s/^$chain /-A $chain /" ;;
esac
EOF
chmod +x "$TMP/iptables"
mkdir -p "$TMP/ufw"
printf '*filter\n:DOCKER-USER - [0:0]\nCOMMIT\n' > "$TMP/ufw/after.rules"
printf '*filter\n:ufw-before-input - [0:0]\n# End required lines\n-A ufw-before-input -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT\nCOMMIT\n' > "$TMP/ufw/before.rules"
run_egress() { IPT_STATE="$STATE" SR_IPTABLES="$TMP/iptables" SR_UFW_DIR="$TMP/ufw" SR_HOST_IP=203.0.113.9 bash "$DIR/host/docker-user-egress.sh" >/dev/null; }
run_egress && run_egress
IN=$(grep '^ufw-before-input ' "$STATE")
FIRST=$(printf '%s\n' "$IN" | sed -n 1p); SECOND=$(printf '%s\n' "$IN" | sed -n 2p)
[ "$(printf '%s\n' "$IN" | grep -c .)" = "2" ] && [[ "$FIRST" == *"--dport 8890 -j ACCEPT" ]] && [[ "$SECOND" == *"-s 172.28.0.0/16 -j DROP" ]] && OK=1 || OK=0
check "INPUT: ACCEPT do proxy antes do DROP do container, sem duplicar ao rodar 2x" "$OK"
[ "$(grep -c 'ufw-before-input -s 172.28.0.0/16 -j DROP' "$TMP/ufw/before.rules")" = "1" ] && \
  [ "$(grep -c -- '--dport 8890 -j ACCEPT' "$TMP/ufw/before.rules")" = "1" ] && OK=1 || OK=0
check "before.rules persiste as 2 regras uma vez só" "$OK"
[ "$(grep -c '^DOCKER-USER ' "$STATE")" = "6" ] && OK=1 || OK=0
check "DOCKER-USER segue com as 6 regras" "$OK"

# ---------- health-stale.sh (SCC-23) ----------
PUB="$TMP/public"; mkdir -p "$PUB/sync-raindrop"
VICTIM="$TMP/victim"; echo "original" > "$VICTIM"; touch -t 202001010000 "$VICTIM"    # velho: dispara o stale
ln -s "$VICTIM" "$PUB/sync-raindrop/health.json.tmp"        # symlink plantado no nome antigo do temporário
ln -s "$VICTIM" "$PUB/sync-raindrop/health.json"            # e no próprio arquivo
touch -h -t 202001010000 "$PUB/sync-raindrop/health.json"   # o stat sem -L lê a data do próprio link
SR_HEALTH_FILE="$PUB/sync-raindrop/health.json" sh "$DIR/host/health-stale.sh"
[ "$(cat "$VICTIM")" = "original" ] && [ ! -L "$PUB/sync-raindrop/health.json" ] && grep -q '"stale"' "$PUB/sync-raindrop/health.json" && OK=1 || OK=0
check "health-stale não segue symlink plantado e grava o stale no lugar" "$OK"
[ -z "$(ls -A "$PUB" | grep '^\.sync-raindrop-health')" ] && OK=1 || OK=0
check "health-stale não deixa temporário para trás" "$OK"

# ---------- teto.sh (SCC-24, SCC-25) ----------
T="$TMP/teto"; mkdir -p "$T/app"
printf 'A=1\nMAX_BATCHES_PER_DAY=12\nB=2\n' > "$T/.env"
cat > "$TMP/docker" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$TMP/docker"
tt() { PATH="$TMP:$PATH" SR_TETO_DIR="$T" sh "$DIR/host/teto.sh" "$@" >/dev/null 2>&1; }
tt '1/;s/A=.*/A=x/'; RC=$?
[ "$RC" != "0" ] && grep -q '^A=1$' "$T/.env" && grep -q '^MAX_BATCHES_PER_DAY=12$' "$T/.env" && [ ! -f "$T/.env.bak-teto-20260930" ] && OK=1 || OK=0
check "teto com valor inválido não toca o arquivo" "$OK"
tt 40 && grep -q '^MAX_BATCHES_PER_DAY=40$' "$T/.env" && OK=1 || OK=0
check "teto 40 muda só a linha do teto" "$OK"
printf 'C=3\n' >> "$T/.env"                                  # mudança no arquivo depois do teto
tt restaura && grep -q '^MAX_BATCHES_PER_DAY=12$' "$T/.env" && grep -q '^C=3$' "$T/.env" && [ ! -f "$T/.env.bak-teto-20260930" ] && OK=1 || OK=0
check "restaura volta só o teto e mantém o que mudou depois" "$OK"

rm -rf "$TMP"
echo "----"
echo "$pass PASS, $fail FAIL"
[ "$fail" -eq 0 ]
