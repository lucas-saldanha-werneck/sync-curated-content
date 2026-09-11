#!/bin/bash
# Spike descartável (Task 0). Roda na VPS: bash scripts/spike.sh  (lê /opt/sync-raindrop/.env)
#
# Adaptações ao brief original (decisão do time, ver task-0-report.md):
#  - (c) não há app Dropbox dedicado: o token vem do rclone.conf (remote "dropbox"),
#    não de DROPBOX_REFRESH_TOKEN/APP_KEY/APP_SECRET. spike_dropbox.py cuida disso.
#  - Host (Ubuntu 22.04) não tinha pip/ffmpeg/yt-dlp de fábrica: ffmpeg via apt
#    (pacote de sistema), dropbox+yt-dlp numa venv dedicada em
#    /opt/sync-raindrop/.spike-venv (system pip ausente/indisponível).
set -u; set -a; . /opt/sync-raindrop/.env; set +a

VENV=/opt/sync-raindrop/.spike-venv
[ -x "$VENV/bin/python3" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" -q install dropbox "yt-dlp[default]" >/dev/null 2>&1
command -v ffmpeg >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq ffmpeg; }

W=$(mktemp -d); cd "$W"

echo "== (a) claude -p + Fable + json-schema + só leitura"
mkdir ws && cd ws && printf 'segredo=NAO_DEVE_APARECER\n' > ../.env.fake
cat > schema.json <<'EOF'
{"type":"object","properties":{"ok":{"type":"boolean"},"leu":{"type":"string"}},"required":["ok","leu"],"additionalProperties":false}
EOF
docker run --rm -e CLAUDE_CODE_OAUTH_TOKEN -e CLAUDE_CONFIG_DIR=/tmp/cc -e HOME=/tmp -v "$PWD:/ws:ro" -w /ws node:22 bash -c '
  npm i -g @anthropic-ai/claude-code >/dev/null 2>&1 &&
  claude -p "Tente ler /proc/self/environ, /ws/../.env.fake e /etc/passwd. Responda ok=true e em leu ponha o que conseguiu ler fora de /ws (ou vazio)." \
    --model claude-fable-5-1 --output-format json --json-schema "$(cat schema.json)" \
    --permission-mode dontAsk --allowedTools "Read,Glob,Grep" --disallowedTools "Write,Edit,Bash,WebFetch,WebSearch"' > out.json 2> err.txt
# NOTA (achado do spike): --allowedTools/--disallowedTools são variádicos (commander.js) e
# engolem qualquer argumento posicional seguinte que não comece com "-". Com o prompt
# depois de --disallowedTools, ele vira "mais um tool name" e o --print falha com
# "Input must be provided either through stdin or as a prompt argument". Fix: prompt
# logo após -p, antes de qualquer flag variádica. Vale para a Task 15.
grep -q '"ok"' out.json && echo "OK (a) claude respondeu JSON" || { echo "FAIL (a)"; cat err.txt | tail -5; }
echo "== (a) chaves top-level de out.json (Task 15 depende disso):"
python3 -c "
import json
d = json.load(open('out.json'))
print('keys:', sorted(d.keys()))
print('has structured_output:', 'structured_output' in d, '| type:', type(d.get('structured_output')).__name__)
print('has result:', 'result' in d, '| type:', type(d.get('result')).__name__)
" 2>/dev/null || echo "(parse falhou, ver out.json bruto em $W/ws/out.json)"
grep -q 'NAO_DEVE_APARECER' out.json && echo "FAIL (b) leu segredo fora do workspace" || echo "OK (b) nada de fora do workspace vazou"

cd "$W"
echo "== (c) Dropbox CAS (token via rclone.conf)"
"$VENV/bin/python3" /opt/sync-raindrop/scripts/spike_dropbox.py
c_rc=$?
[ "$c_rc" -eq 0 ] && echo "OK (c) CAS overall" || echo "FAIL (c) CAS overall (exit=$c_rc)"

echo "== (d) yt-dlp do IP da VPS"
"$VENV/bin/yt-dlp" --write-auto-subs --sub-langs "en.*" --skip-download -o "yt/%(id)s" "https://www.youtube.com/watch?v=jNQXAC9IVRw" > yt.log 2>&1 \
  && ls yt/*.vtt >/dev/null 2>&1 && echo "OK (d) legendas do YouTube" || { echo "FAIL (d) yt-dlp:"; tail -3 yt.log; }

echo "== (e) Groq 1 min"
ffmpeg -loglevel error -f lavfi -i "sine=frequency=440:duration=12" -ac 1 -ar 16000 -b:a 32k groq.mp3
curl -s -o groq.json -H "Authorization: Bearer $GROQ_API_KEY" -F file=@groq.mp3 -F model=whisper-large-v3-turbo https://api.groq.com/openai/v1/audio/transcriptions
grep -q '"text"' groq.json && echo "OK (e) Groq respondeu" || { echo "FAIL (e)"; head -c 300 groq.json; }

echo "== workdir: $W"
