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
    downloads = []
    def fake_fetch_captions(url, out_dir):
        (Path(out_dir) / "v.en.vtt").write_text((FIX / "sample.vtt").read_text())
        return {"subtitle_path": str(Path(out_dir) / "v.en.vtt"), "info": {"title": "T", "uploader": "U", "duration": 42}}
    def fake_download_media(url, out_dir, audio_only, max_mb=None):
        downloads.append((url, audio_only))
        return str(Path(out_dir) / "v.mp4")
    monkeypatch.setattr("lib.fetch_video.fetch_captions", fake_fetch_captions)
    monkeypatch.setattr("lib.fetch_video.download_media", fake_download_media)
    monkeypatch.setattr("lib.fetch_video.extract_frames", lambda *a, **k: [("01_0000.jpg", b"\xff\xd8jpg")])
    r = fetch_video("https://youtube.com/watch?v=x", tmp_path, cfg, "gk", "youtube")
    assert r.method == "captions" and "hello world" in r.text and r.title == "T" and r.duration_s == 42
    assert r.frames and r.frames[0][0] == "01_0000.jpg"
    # captions+frames: exatamente 1 download (o vídeo, para os frames — legendas não baixam mídia)
    assert len(downloads) == 1

def test_fetch_video_groq_fallback_counts_seconds(tmp_path, monkeypatch):
    cfg = Config(); used = []; downloads = []
    monkeypatch.setattr("lib.fetch_video.fetch_captions", lambda url, out: {"subtitle_path": None, "info": {"title": "T", "duration": 90}})
    def fake_download_media(url, out_dir, audio_only, max_mb=None):
        downloads.append((url, audio_only))
        return str(Path(out_dir) / "a.mp4")
    monkeypatch.setattr("lib.fetch_video.download_media", fake_download_media)
    monkeypatch.setattr("lib.fetch_video.groq_transcribe", lambda path, key: ("texto via groq", 90.0))
    monkeypatch.setattr("lib.fetch_video.extract_frames", lambda *a, **k: [])
    r = fetch_video("https://tiktok.com/@u/video/1", tmp_path, cfg, "gk", "tiktok", journal_add_groq=used.append)
    assert r.method == "groq" and r.text == "texto via groq" and used == [90]
    # groq+frames (frames_max>0 por padrão, duração dentro do limite): 1 download só,
    # do vídeo completo (audio_only=False) — reaproveitado p/ groq_transcribe e p/ frames.
    assert len(downloads) == 1 and downloads[0][1] is False

def test_fetch_video_groq_quota_exhausted(tmp_path, monkeypatch):
    cfg = Config()
    monkeypatch.setattr("lib.fetch_video.fetch_captions", lambda url, out: {"subtitle_path": None, "info": {"duration": 100}})
    r = fetch_video("https://tiktok.com/@u/video/1", tmp_path, cfg, "gk", "tiktok", groq_seconds_used_today=cfg.groq_daily_seconds - 10)
    assert r.method == "none" and "groq_quota" in (r.error or "")

def test_fetch_video_duration_over_limit_skips_download_and_frames(tmp_path, monkeypatch):
    cfg = Config()
    downloads = []
    over_seconds = cfg.media_max_min * 60 + 60
    monkeypatch.setattr("lib.fetch_video.fetch_captions", lambda url, out: {"subtitle_path": None, "info": {"title": "T", "duration": over_seconds}})
    monkeypatch.setattr("lib.fetch_video.download_media", lambda *a, **k: (downloads.append(1), "x")[1])
    r = fetch_video("https://tiktok.com/@u/video/2", tmp_path, cfg, "gk", "tiktok")
    assert r.method == "none" and str(cfg.media_max_min) in (r.error or "")
    assert r.frames == [] and downloads == []
