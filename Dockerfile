FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 TZ=America/Sao_Paulo

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg poppler-utils git curl ca-certificates tinyproxy gosu tzdata unzip \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm i -g @anthropic-ai/claude-code \
    && curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.deb -o /tmp/rclone.deb \
    && apt-get install -y /tmp/rclone.deb && rm /tmp/rclone.deb \
    && curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip && chmod 755 /usr/local/bin/deno \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 app       && useradd -u 10001 -g app       -m -d /home/app app \
    && groupadd -g 10002 app-fetch && useradd -u 10002 -g app-fetch -M -s /usr/sbin/nologin app-fetch \
    && groupadd -g 10003 app-claude && useradd -u 10003 -g app-claude -M -s /usr/sbin/nologin app-claude \
    # app entrega o workspace de destilação ao claude pelo GRUPO (chgrp sem capability; ver lib/distill.build_workspace)
    && usermod -aG app-claude app

RUN pip install --no-cache-dir "yt-dlp[default]" dropbox trafilatura bgutil-ytdlp-pot-provider

# PO-token provider para o bot-wall do YouTube (best-effort). Se o clone/build falhar,
# a imagem segue sem ele; o entrypoint detecta a ausência e não define YTDLP_POT_URL.
RUN ( git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider /opt/bgutil \
      && cd /opt/bgutil/server \
      && npm ci \
      && npx tsc \
    ) || echo "bgutil-ytdlp-pot-provider: build falhou, seguindo sem PO-token" >&2

WORKDIR /app
COPY . /app
RUN chmod +x /app/entrypoint.sh /app/bin/sync.sh \
    && mkdir -p /etc/tinyproxy \
    && cp /app/tinyproxy.conf /etc/tinyproxy/tinyproxy.conf \
    && cp /app/tinyproxy.filter /etc/tinyproxy/filter

ENV YTDLP_PROXY=http://127.0.0.1:8888 \
    HEALTH_PUBLIC_DIR=/public/sync-raindrop \
    RCLONE_CONF=/secrets/rclone/rclone.conf

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["run"]
