#!/bin/bash
set -euo pipefail

mkdir -p /var/lib/sync-raindrop /public/sync-raindrop /tmp/work
chown -R app:app /var/lib/sync-raindrop /public/sync-raindrop /tmp/work

tinyproxy -c /etc/tinyproxy/tinyproxy.conf

# Segredos: só lidos aqui (como root, antes de trocar para o uid app) e passados
# como ambiente do subprocesso; nada fica gravado em disco além do /secrets/.env
# montado read-only pelo compose.
set -a
# shellcheck disable=SC1091
. /secrets/.env
set +a

# PO-token provider do yt-dlp (bgutil) — opcional. Sobe só se o build existir na imagem.
BGUTIL_MAIN=""
for f in /opt/bgutil/server/build/main.js /opt/bgutil/build/main.js; do
  [ -f "$f" ] && BGUTIL_MAIN="$f" && break
done
if [ -n "$BGUTIL_MAIN" ]; then
  ( cd "$(dirname "$BGUTIL_MAIN")/.." && node "$BGUTIL_MAIN" --port 4416 --host 127.0.0.1 & )
  export YTDLP_POT_URL=http://127.0.0.1:4416
else
  echo "bgutil PO-token provider ausente — YTDLP_POT_URL não definido" >&2
fi

exec gosu app python3 /app/bin/sync.py "$@"
