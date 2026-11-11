#!/bin/sh
# teto.sh <N> | restaura  — muda só MAX_BATCHES_PER_DAY no arquivo de segredos do sync-raindrop; não imprime valores.
set -e
F=/opt/sync-raindrop/.env
B=/opt/sync-raindrop/.env.bak-teto-20260930
if [ "$1" = "restaura" ]; then
  cp -p "$B" "$F" && rm "$B"
else
  [ -f "$B" ] || cp -p "$F" "$B"
  sed -i "s/^MAX_BATCHES_PER_DAY=.*/MAX_BATCHES_PER_DAY=$1/" "$F"
fi
N=$(grep '^MAX_BATCHES_PER_DAY=' "$F" | cut -d= -f2)
case "$N" in ''|*[!0-9]*) echo falhou; exit 1;; esac
echo "ok: teto=$N"
cd /opt/sync-raindrop/app && docker compose up -d --force-recreate >/dev/null 2>&1 && echo recriado
