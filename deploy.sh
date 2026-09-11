#!/bin/zsh
set -euo pipefail
HOST="${1:-nova}"
HERE="$(cd "$(dirname "$0")" && pwd)"

rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.superpowers' --exclude 'PLAN*.md' --exclude 'DESIGN.md' \
  "$HERE/" "$HOST:/opt/sync-raindrop/app/"

ssh "$HOST" 'set -e
  test -f /opt/sync-raindrop/.env || { echo "falta /opt/sync-raindrop/.env"; exit 1; }
  test -f /opt/sync-raindrop/rclone.conf || cp /root/.config/rclone/rclone.conf /opt/sync-raindrop/rclone.conf
  chmod 600 /opt/sync-raindrop/.env
  chmod 600 /opt/sync-raindrop/rclone.conf
  chown 10001:10001 /opt/sync-raindrop/rclone.conf
  mkdir -p /var/lib/sync-raindrop /opt/files/public/sync-raindrop
  chown -R 10001:10001 /var/lib/sync-raindrop /opt/files/public/sync-raindrop
  install -m 755 /opt/sync-raindrop/app/host/health-stale.sh /usr/local/bin/sync-raindrop-health-stale
  ( crontab -l 2>/dev/null | grep -v sync-raindrop-health-stale; echo "*/5 * * * * /usr/local/bin/sync-raindrop-health-stale" ) | crontab -
  cd /opt/sync-raindrop/app
  docker compose build --pull
  docker compose up -d
  sleep 5
  docker ps --filter name=sync-raindrop --format "{{.Names}} {{.Status}}"
  docker logs --tail 20 sync-raindrop'
