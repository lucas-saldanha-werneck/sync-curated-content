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
