#!/bin/bash
# Egress do container sync-raindrop (sub-rede fixa 172.28.0.0/16, ver docker-compose.yml):
# bloqueia destino RFC1918 / link-local / CGNAT / IP público do próprio host na chain DOCKER-USER (FORWARD).
# Defesa em profundidade: o guard SSRF do app + tinyproxy já filtram por hostname; isto fecha o que for por IP.
# Idempotente: aplica ao vivo (iptables -C || -I) e persiste em /etc/ufw/after.rules (bloco DOCKER-USER já existente).
set -euo pipefail
SRC="172.28.0.0/16"
HOST_IP="$(hostname -I | awk '{print $1}')"
DSTS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 ${HOST_IP}/32"
RULES=""
for d in $DSTS; do
  iptables -C DOCKER-USER -s "$SRC" -d "$d" -j DROP 2>/dev/null || iptables -I DOCKER-USER 1 -s "$SRC" -d "$d" -j DROP
  RULES="${RULES}-A DOCKER-USER -s ${SRC} -d ${d} -j DROP\n"
done
AR=/etc/ufw/after.rules
# persistência: checa pela própria regra (não pelo comentário) e usa printf -- (a linha começa com "-A")
if ! grep -q -- "-s ${SRC} -d 10.0.0.0/8 -j DROP" "$AR"; then
  python3 - "$AR" "$(printf -- "$RULES")" <<'PY'
import sys, re
path, rules = sys.argv[1], sys.argv[2]
s = open(path).read()
marker = ":DOCKER-USER - [0:0]\n"
assert marker in s, "bloco DOCKER-USER não encontrado em after.rules"
comment = "# sync-raindrop egress (host/docker-user-egress.sh): nada de RFC1918/link-local/CGNAT/host a partir de 172.28.0.0/16\n"
s = s.replace(comment + "\n", "", 1).replace(comment, "", 1)   # limpa marcador órfão de uma versão anterior
s = s.replace(marker, marker + comment + rules + "\n", 1)
open(path, "w").write(s)
PY
fi
echo "egress sync-raindrop: $(iptables -S DOCKER-USER | grep -c "$SRC") regras ativas; after.rules: $(grep -c "$SRC" "$AR") linhas"
