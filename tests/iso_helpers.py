"""Fetchers falsos para os testes com isolate=True. O worker isolado roda em processo "spawn" (interpretador
novo), então a função e os argumentos precisam ser serializáveis: funções de módulo, nada de lambda/closure."""
import json, os, subprocess, time
from lib.fetch_types import FetchResult

MARK = "valor-de-fabrica"          # o teste muda no pai; um filho "spawn" não pode enxergar a mudança


def sleepy_article(url, cfg, jk, fk, **k):
    time.sleep(5)


def big_article(url, cfg, jk, fk, **k):
    return FetchResult(platform="article", text="t" * 500, method="trafilatura", frames=[("f.jpg", b"x" * 300_000)])


def crash_article(url, cfg, jk, fk, **k):
    os._exit(1)


def probe_article(url, cfg, jk, fk, **k):
    """Devolve o que o filho enxerga: segredos do cfg e a variável global do módulo."""
    seen = {"mark": MARK, "raindrop_token": cfg.raindrop_token, "claude_oauth_token": cfg.claude_oauth_token,
            "groq_api_key": cfg.groq_api_key, "apify_token": cfg.apify_token, "jk": jk}
    return FetchResult(platform="article", text=json.dumps(seen), method="trafilatura")


def sleeper_video(pidfile, hold_s, url, work, cfg, groq_key, platform, **kwargs):
    """yt-dlp/ffmpeg travado: um neto dorme e o worker fica preso (até hold_s)."""
    p = subprocess.Popen(["sleep", "30"])
    with open(pidfile, "w") as f:
        f.write(str(p.pid))
    if hold_s:
        time.sleep(hold_s)
    else:
        p.wait()
    return FetchResult(platform=platform, method="none")
