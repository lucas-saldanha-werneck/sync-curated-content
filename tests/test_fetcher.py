import pytest

from lib.fetcher import route, fetch
from lib.config import Config
from lib.fetch_types import FetchResult


def test_route():
    assert route("https://youtu.be/x", "video") == "youtube"
    assert route("https://www.youtube.com/shorts/x", "link") == "youtube"
    assert route("https://vm.tiktok.com/ZM/", "link") == "tiktok"
    assert route("https://www.instagram.com/reel/a/", "link") == "instagram"
    assert route("https://www.linkedin.com/posts/x", "link") == "linkedin"
    assert route("https://x.com/a/status/1", "link") == "x"
    assert route("https://site.com/f.pdf", "link") == "pdf" and route("https://site.com/f", "document") == "pdf"
    assert route("https://blog.com/a", "article") == "article"


def test_fetch_dispatch_in_process(tmp_path):
    calls = []
    fns = {"article": lambda url, cfg, jk, fk, **k: (calls.append(url) or FetchResult(platform="article", text="t" * 500, method="trafilatura"))}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(), {"jina": None, "firecrawl": None}, journal=None, fns=fns, isolate=False)
    assert r.method == "trafilatura" and calls == ["https://blog.com/a"]


def test_fetch_timeout(tmp_path):
    import time
    fns = {"article": lambda url, cfg, jk, fk, **k: time.sleep(5)}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(fetch_timeout_s=1), {}, journal=None, fns=fns, isolate=True)
    assert r.method == "none" and "timeout" in r.error


def test_large_result_not_misreported_as_timeout(tmp_path):
    """Fix round 1 (crítico): q.get() precisa vir ANTES do p.join(), senão um resultado
    grande (frames JPEG) enche o pipe e o filho fica travado no put() até o pai drenar —
    e o pai só chegava a drenar depois de esgotar o timeout inteiro, reportando timeout
    num fetch que na verdade tinha terminado."""
    import time
    fns = {"article": lambda url, cfg, jk, fk, **k: FetchResult(
        platform="article", text="t" * 500, method="trafilatura",
        frames=[("f.jpg", b"x" * 300_000)])}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    t0 = time.monotonic()
    r = fetch(item, tmp_path, Config(fetch_timeout_s=5), {}, journal=None, fns=fns, isolate=True)
    elapsed = time.monotonic() - t0
    assert r.method == "trafilatura"
    assert elapsed < 3, f"deveria voltar quase na hora, levou {elapsed:.1f}s (ficou preso até o timeout)"


def test_timeout_kills_process_group(tmp_path):
    """Fix round 1 (crítico): no timeout, o worker precisa ser líder de grupo (os.setsid())
    e o pai precisa matar o GRUPO (os.killpg) — senão netos como yt-dlp/ffmpeg ficam
    órfãos rodando depois do timeout."""
    import os as _os
    import subprocess
    import time
    pidfile = tmp_path / "sleeper.pid"

    def fake_video_fetch(url, work, cfg, groq_key, platform, **kwargs):
        p = subprocess.Popen(["sleep", "30"])
        pidfile.write_text(str(p.pid))
        time.sleep(10)
        return FetchResult(platform=platform, method="none")

    fns = {"video": fake_video_fetch}
    item = {"id": 1, "link": "https://youtu.be/x", "type": "video", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(fetch_timeout_s=1), {}, journal=None, fns=fns, isolate=True)
    assert r.method == "none" and "timeout" in r.error

    time.sleep(0.5)  # dá tempo do SIGKILL propagar pro grupo inteiro
    sleeper_pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        _os.kill(sleeper_pid, 0)  # processo morto: sinal 0 levanta ProcessLookupError


def test_x_video_groq_is_journaled(tmp_path):
    """Fix round 1 (importante): quando o X embute vídeo, os segundos de Groq gastos na
    transcrição do vídeo precisam ir pro journal (res.method continua "fxtwitter", só o
    sub-fetch de vídeo é "groq")."""
    from lib.journal import Journal
    fns = {
        "x": lambda url, cfg, **k: FetchResult(platform="x", method="fxtwitter", text="post",
                                                extra={"video_url": "https://v"}),
        "video": lambda url, work, cfg, groq_key, platform, **k: FetchResult(
            platform="x", method="groq", text="fala", duration_s=42),
    }
    item = {"id": 1, "link": "https://x.com/a/status/1", "type": "link", "title": "", "tags": []}
    j = Journal(tmp_path / "j.db")
    cfg = Config()
    r = fetch(item, tmp_path, cfg, {"groq": "gk"}, journal=j, fns=fns, isolate=False)
    assert j.count_groq_seconds_today(cfg.tz) == 42
    j.close()
    assert r.method == "fxtwitter" and "fala" in r.text and r.extra.get("video_method") == "groq"


def test_isolate_false_never_raises(tmp_path):
    """Fix round 1 (importante): com isolate=False uma exceção do fetcher não pode
    propagar — vira FetchResult(method="none", error=...)."""
    def boom(url, cfg, jk, fk, **k):
        raise ValueError("boom")
    fns = {"article": boom}
    item = {"id": 1, "link": "https://blog.com/a", "type": "article", "title": "", "tags": []}
    r = fetch(item, tmp_path, Config(), {}, journal=None, fns=fns, isolate=False)
    assert r.method == "none" and "boom" in r.error
