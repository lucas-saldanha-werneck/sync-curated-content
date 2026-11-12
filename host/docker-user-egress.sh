#!/bin/bash
# Egress do container sync-raindrop (sub-rede fixa 172.28.0.0/16, ver docker-compose.yml):
# 1) FORWARD (chain DOCKER-USER): bloqueia destino RFC1918 / link-local / CGNAT / IP público do próprio host.
# 2) INPUT (chain ufw-before-input): tráfego do container para o PRÓPRIO host não passa pela FORWARD, entra pela
#    INPUT. Lá só o proxy do host (172.28.0.1:8890) é aceito; o resto (SSH, 80/443 do host...) cai.
# Defesa em profundidade: o guard SSRF do app + tinyproxy já filtram por hostname; isto fecha o que for por IP.
# Idempotente: aplica ao vivo e persiste em /etc/ufw/after.rules (DOCKER-USER) e /etc/ufw/before.rules (INPUT).
set -euo pipefail
SRC="172.28.0.0/16"
PROXY_IP="${SR_PROXY_IP:-172.28.0.1}"; PROXY_PORT="${SR_PROXY_PORT:-8890}"
UFW_DIR="${SR_UFW_DIR:-/etc/ufw}"
IPT="${SR_IPTABLES:-iptables}"
HOST_IP="${SR_HOST_IP:-$(hostname -I | awk '{print $1}')}"
DSTS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 ${HOST_IP}/32"
RULES=""
for d in $DSTS; do
  $IPT -C DOCKER-USER -s "$SRC" -d "$d" -j DROP 2>/dev/null || $IPT -I DOCKER-USER 1 -s "$SRC" -d "$d" -j DROP
  RULES="${RULES}-A DOCKER-USER -s ${SRC} -d ${d} -j DROP\n"
done

# INPUT: a ordem importa (ACCEPT do proxy antes do DROP). Tira as duas e põe de novo, na ordem certa.
ACC=(-s "$SRC" -d "$PROXY_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT)
DRP=(-s "$SRC" -j DROP)
while $IPT -C ufw-before-input "${ACC[@]}" 2>/dev/null; do $IPT -D ufw-before-input "${ACC[@]}"; done
while $IPT -C ufw-before-input "${DRP[@]}" 2>/dev/null; do $IPT -D ufw-before-input "${DRP[@]}"; done
$IPT -I ufw-before-input 1 "${DRP[@]}"
$IPT -I ufw-before-input 1 "${ACC[@]}"

AR="$UFW_DIR/after.rules"
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

BR="$UFW_DIR/before.rules"
if ! grep -q -- "-A ufw-before-input -s ${SRC} -j DROP" "$BR"; then
  python3 - "$BR" "-A ufw-before-input ${ACC[*]}" "-A ufw-before-input ${DRP[*]}" <<'PY'
import sys
path, acc, drp = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
anchor = "# End required lines\n"
assert anchor in s, "'# End required lines' não encontrado em before.rules"
block = ("\n# sync-raindrop (host/docker-user-egress.sh): do container para o host, só o proxy\n"
         f"{acc}\n{drp}\n")
s = s.replace(anchor, anchor + block, 1)
open(path, "w").write(s)
PY
fi
echo "egress sync-raindrop: $($IPT -S DOCKER-USER | grep -c "$SRC") regras DOCKER-USER, $($IPT -S ufw-before-input | grep -c "$SRC") INPUT; after.rules: $(grep -c "$SRC" "$AR") linhas; before.rules: $(grep -c "$SRC" "$BR") linhas"
