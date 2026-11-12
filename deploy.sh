#!/bin/zsh
set -euo pipefail
HOST="${1:-nova}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# lista em deploy.exclude (testada em tests/test_deploy_exclude.py). --delete-excluded tira da VPS o que já
# tinha ido antes (ex.: ".venv 2", ".git.parcial-*"): nada excluído aqui é necessário em /opt/sync-raindrop/app.
rsync -az --delete --delete-excluded --exclude-from="$HERE/deploy.exclude" \
  "$HERE/" "$HOST:/opt/sync-raindrop/app/"

ssh "$HOST" 'set -e
  test -f /opt/sync-raindrop/.env || { echo "falta /opt/sync-raindrop/.env"; exit 1; }
  # rclone reescreve o conf via arquivo temporário na MESMA pasta: a pasta precisa ser montada (rw), não só o arquivo
  mkdir -p /opt/sync-raindrop/rclone
  test -f /opt/sync-raindrop/rclone/rclone.conf || { test -f /opt/sync-raindrop/rclone.conf && mv /opt/sync-raindrop/rclone.conf /opt/sync-raindrop/rclone/rclone.conf; }
  # só o remote [dropbox] (o serviço não usa os outros remotes do root); se o arquivo já existe, só tira os outros
  python3 /opt/sync-raindrop/app/host/rclone_only_remote.py /root/.config/rclone/rclone.conf /opt/sync-raindrop/rclone/rclone.conf dropbox
  chmod 600 /opt/sync-raindrop/.env
  chmod 700 /opt/sync-raindrop/rclone
  chmod 600 /opt/sync-raindrop/rclone/rclone.conf
  chown -R 10001:10001 /opt/sync-raindrop/rclone
  mkdir -p /var/lib/sync-raindrop /opt/files/public/sync-raindrop
  chown -R 10001:10001 /var/lib/sync-raindrop /opt/files/public/sync-raindrop
  install -m 755 /opt/sync-raindrop/app/host/health-stale.sh /usr/local/bin/sync-raindrop-health-stale
  ( crontab -l 2>/dev/null | grep -v sync-raindrop-health-stale; echo "*/5 * * * * /usr/local/bin/sync-raindrop-health-stale" ) | crontab -
  # o serviço roda como app, não root: pasta local 700 vira 700 no /app e o container cai em
  # "Permission denied" (11/11/2026; o rsync do macOS ignora --chmod). Libera leitura antes do build.
  chmod -R go+rX /opt/sync-raindrop/app
  bash /opt/sync-raindrop/app/host/docker-user-egress.sh
  cd /opt/sync-raindrop/app
  docker compose build --pull
  docker compose up -d
  sleep 5
  docker ps --filter name=sync-raindrop --format "{{.Names}} {{.Status}}"
  docker logs --tail 20 sync-raindrop'
