#!/bin/sh
# Roda a cada 5 min via cron no host. Se o health.json público não foi atualizado
# há muito tempo (container travado/caído), marca "stale" para o monitor externo
# (UptimeRobot) detectar.
#
# O limite é 20 min, não 5: o ciclo do orquestrador é sequencial e só grava o health
# ENTRE lotes. Um lote legítimo pode levar até claude_timeout_s (900 s = 15 min), então
# 5 min alarmava durante backfill normal (falso positivo, 2026-09-12). 20 min > 1 lote
# no teto + folga, e ainda pega container travado de verdade.
#
# Roda como root, mas a pasta é do uid do container (10001): nada de gravar "$F.tmp" lá dentro (um symlink
# plantado faria o root escrever onde o container quisesse). O temporário nasce na pasta-mãe (do root, mesma
# partição) e entra no lugar por rename(2), que troca a entrada e nunca segue symlink.
F="${SR_HEALTH_FILE:-/opt/files/public/sync-raindrop/health.json}"
STALE_AFTER=1200
if [ ! -f "$F" ] || [ $(( $(date +%s) - $(stat -c %Y "$F" 2>/dev/null || stat -f %m "$F") )) -gt "$STALE_AFTER" ]; then
  P="$(dirname "$(dirname "$F")")"
  T="$(mktemp "$P/.sync-raindrop-health.XXXXXX")" || exit 1
  if printf '{"ok":false,"reason":"stale","ts":"%s"}\n' "$(date -u +%FT%TZ)" > "$T" && chmod 644 "$T" \
     && python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$T" "$F"; then
    :
  else
    rm -f "$T"; exit 1
  fi
fi
