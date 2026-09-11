#!/bin/sh
# Roda a cada 5 min via cron no host. Se o health.json público não foi
# atualizado nos últimos 5 min (container travado/caído), marca "stale"
# para o monitor externo (UptimeRobot) detectar.
F=/opt/files/public/sync-raindrop/health.json
if [ ! -f "$F" ] || [ $(( $(date +%s) - $(stat -c %Y "$F") )) -gt 300 ]; then
  printf '{"ok":false,"reason":"stale","ts":"%s"}\n' "$(date -u +%FT%TZ)" > "$F.tmp" && mv "$F.tmp" "$F"
fi
