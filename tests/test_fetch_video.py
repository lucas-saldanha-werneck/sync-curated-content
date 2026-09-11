from pathlib import Path
from lib.config import Config
from lib.fetch_video import fetch_video
from lib.video.transcribe import parse_vtt, format_transcript

FIX = Path(__file__).parent / "fixtures"

def test_parse_vtt_dedupes_rolling():
    segs = parse_vtt(str(FIX / "sample.vtt"))
    txt = format_transcript(segs)
    assert "hello world" in txt and txt.count("hello world") == 1 and "this is a test" in txt

def test_fetch_video_captions_path(tmp_path, monkeypatch):
    cfg = Config()
    def fake_fetch_captions(url, out_dir):
        (Path(out_dir) / "v.en.vtt").write_text((FIX / "sample.vtt").read_text())
        return {"subtitle_path": str(Path(out_dir) / "v.en.vtt"), "info": {"title": "T", "uploader": "U", "duration": 42}}
    monkeypatch.setattr("lib.fetch_video.fetch_captions", fake_fetch_captions)
    monkeypatch.setattr("lib.fetch_video.extract_frames", lambda *a, **k: [("01_0000.jpg", b"\xff\xd8jpg")])
    r = fetch_video("https://youtube.com/watch?v=x", tmp_path, cfg, "gk", "youtube")
    assert r.method == "captions" and "hello world" in r.text and r.title == "T" and r.duration_s == 42
    assert r.frames and r.frames[0][0] == "01_0000.jpg"

def test_fetch_video_groq_fallback_counts_seconds(tmp_path, monkeypatch):
    cfg = Config(); used = []
    monkeypatch.setattr("lib.fetch_video.fetch_captions", lambda url, out: {"subtitle_path": None, "info": {"title": "T", "duration": 90}})
    monkeypatch.setattr("lib.fetch_video.download_audio", lambda url, out, max_min: str(Path(out) / "a.mp3"))
    monkeypatch.setattr("lib.fetch_video.groq_transcribe", lambda path, key: ("texto via groq", 90.0))
    monkeypatch.setattr("lib.fetch_video.extract_frames", lambda *a, **k: [])
    r = fetch_video("https://tiktok.com/@u/video/1", tmp_path, cfg, "gk", "tiktok", journal_add_groq=used.append)
    assert r.method == "groq" and r.text == "texto via groq" and used == [90]

def test_fetch_video_groq_quota_exhausted(tmp_path, monkeypatch):
    cfg = Config()
    monkeypatch.setattr("lib.fetch_video.fetch_captions", lambda url, out: {"subtitle_path": None, "info": {"duration": 100}})
    r = fetch_video("https://tiktok.com/@u/video/1", tmp_path, cfg, "gk", "tiktok", groq_seconds_used_today=cfg.groq_daily_seconds - 10)
    assert r.method == "none" and "groq_quota" in (r.error or "")
