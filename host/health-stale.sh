#!/bin/sh
# Roda a cada 5 min via cron no host. Se o health.json público não foi atualizado
# há muito tempo (container travado/caído), marca "stale" para o monitor externo
# (UptimeRobot) detectar.
#
# O limite é 20 min, não 5: o ciclo do orquestrador é sequencial e só grava o health
# ENTRE lotes. Um lote legítimo pode levar até claude_timeout_s (900 s = 15 min), então
# 5 min alarmava durante backfill normal (falso positivo, 2026-09-12). 20 min > 1 lote
# no teto + folga, e ainda pega container travado de verdade.
F=/opt/files/public/sync-raindrop/health.json
STALE_AFTER=1200
if [ ! -f "$F" ] || [ $(( $(date +%s) - $(stat -c %Y "$F") )) -gt "$STALE_AFTER" ]; then
  printf '{"ok":false,"reason":"stale","ts":"%s"}\n' "$(date -u +%FT%TZ)" > "$F.tmp" && mv "$F.tmp" "$F"
fi
