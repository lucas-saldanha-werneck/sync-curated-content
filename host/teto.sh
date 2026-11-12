#!/bin/sh
# teto.sh <N> | restaura  — muda só MAX_BATCHES_PER_DAY no arquivo de segredos do sync-raindrop; não imprime valores.
# restaura volta só ESSA linha ao valor de antes do primeiro `teto N` (o resto do arquivo fica como está agora).
set -e
D="${SR_TETO_DIR:-/opt/sync-raindrop}"
F="$D/.env"
B="$D/.env.bak-teto-20260930"
K=MAX_BATCHES_PER_DAY
if [ "${1:-}" = "restaura" ]; then
  [ -f "$B" ] || { echo "sem backup do teto: nada a restaurar"; exit 1; }
  OLD=$(grep "^$K=" "$B" | cut -d= -f2-) || OLD=""
  case "$OLD" in
    '') grep -v "^$K=" "$F" > "$F.new" || true ;;                       # não existia antes: tira a linha
    *[!0-9]*) echo "valor do backup não é número: não mexi"; exit 1 ;;
    *) sed "s/^$K=.*/$K=$OLD/" "$F" > "$F.new" ;;
  esac
  chmod 600 "$F.new"; mv "$F.new" "$F"; rm "$B"
  echo "ok: teto restaurado"
else
  case "${1:-}" in ''|*[!0-9]*) echo "uso: teto.sh <número> | restaura"; exit 1;; esac   # valida ANTES de mexer
  [ -f "$B" ] || cp -p "$F" "$B"
  if grep -q "^$K=" "$F"; then
    sed "s/^$K=.*/$K=$1/" "$F" > "$F.new"
  else
    { cat "$F"; echo "$K=$1"; } > "$F.new"
  fi
  chmod 600 "$F.new"; mv "$F.new" "$F"
  N=$(grep "^$K=" "$F" | cut -d= -f2)
  case "$N" in ''|*[!0-9]*) echo falhou; exit 1;; esac
  echo "ok: teto=$N"
fi
cd "$D/app" && docker compose up -d --force-recreate >/dev/null 2>&1 && echo recriado
