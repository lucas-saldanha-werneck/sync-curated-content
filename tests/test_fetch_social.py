import json
from lib.config import Config
from lib.fetch_social import fetch_instagram, fetch_x, fetch_linkedin

def test_instagram_caption_and_video(tmp_path):
    def opener(u, **k):
        return (json.dumps([{"caption": "legenda do reel", "videoUrl": "https://cdn/v.mp4", "ownerUsername": "lucas"}]).encode(), u, {})
    vid = lambda url, *a, **k: type("R", (), {"text": "fala do vídeo", "method": "groq", "duration_s": 30, "frames": []})()
    r = fetch_instagram("https://www.instagram.com/reel/abc/", tmp_path, Config(), "tok", "gk", opener=opener, video_fn=vid)
    assert r.method == "apify" and "legenda do reel" in r.text and "fala do vídeo" in r.text and r.author == "lucas"

def test_instagram_without_token(tmp_path):
    r = fetch_instagram("https://instagram.com/p/1", tmp_path, Config(), None, "gk")
    assert r.method == "none" and "apify" in r.error

def test_x_text():
    opener = lambda u, **k: (json.dumps({"tweet": {"text": "um tweet", "author": {"screen_name": "a"}, "media": {"videos": [{"url": "https://v"}]}}}).encode(), u, {})
    r = fetch_x("https://x.com/a/status/123", Config(), opener=opener)
    assert r.method == "fxtwitter" and r.text == "um tweet" and r.extra["video_url"] == "https://v"

def test_linkedin_without_token():
    assert fetch_linkedin("https://linkedin.com/posts/x", Config(), None).method == "none"
