from pathlib import Path
from lib.capture import capture_one, Deps
from lib.config import Config
from lib.records import Records
from lib.journal import Journal
from lib.fetch_types import FetchResult
from tests.test_dropbox_store import make as make_store

ITEM = {"id": 5, "link": "https://youtu.be/x", "title": "Vídeo", "excerpt": "", "note": "", "tags": ["a"], "type": "video",
        "created": "2026-09-10T20:00:00.000Z", "cover": "", "domain": "youtu.be", "raw": {"_id": 5}}

class FakeRD:
    def __init__(self, item): self.item = dict(item); self.puts = []
    def get(self, i): return dict(self.item)
    def merge_tags(self, i, add, remove, note_suffix=None): self.puts.append((add, remove)); return self.item

def deps(tmp_path, fetch_result, item=ITEM):
    store = make_store(); cfg = Config()
    return Deps(cfg=cfg, raindrop=FakeRD(item), store=store, records=Records(store, cfg), journal=Journal(tmp_path / "j.db"),
                fetch_fn=lambda it, work, cfg, secrets, journal: fetch_result, secrets={}), store

def test_capture_success_writes_raw_frames_records_tags(tmp_path):
    d, store = deps(tmp_path, FetchResult(platform="youtube", text="conteudo", method="captions", frames=[("01_0000.jpg", b"jpg")]))
    assert capture_one(ITEM, d, tmp_path) == "captured"
    paths = list(store._client.files)
    assert any(p.endswith("_youtube-video-r5.md") for p in paths) and any(p.endswith("/frames/01_0000.jpg") for p in paths)
    assert any(p.endswith("r5.meta.json") for p in paths) and any(p.endswith("r5.captured.json") for p in paths)
    assert d.raindrop.puts[-1] == (["sr:raw"], ["sr:retry"]) and d.journal.get_item(5)["state"] == "captured"

def test_capture_failure_goes_retry_then_partial(tmp_path):
    d, store = deps(tmp_path, FetchResult(platform="youtube", method="none", error="yt-dlp: x"))
    assert capture_one(ITEM, d, tmp_path) == "retry" and any(p.endswith("r5.retry1.json") for p in store._client.files)
    for _ in range(2):
        d.records.refresh(); capture_one(ITEM, d, tmp_path)
    d.records.refresh()
    assert capture_one(ITEM, d, tmp_path) == "captured"          # 4ª vez: 3 retries feitos → raw só de metadados
    md = [v[0] for p, v in store._client.files.items() if p.endswith("-r5.md")][0].decode()
    assert 'transcript_method: "none"' in md

def test_capture_nowiki_revalidated(tmp_path):
    d, store = deps(tmp_path, FetchResult(platform="youtube", text="x", method="captions"), item={**ITEM, "tags": ["nowiki"]})
    assert capture_one(ITEM, d, tmp_path) == "nowiki" and any(p.endswith("r5.nowiki.json") for p in store._client.files)

def test_capture_idempotent_on_rerun(tmp_path):
    d, store = deps(tmp_path, FetchResult(platform="youtube", text="conteudo", method="captions"))
    capture_one(ITEM, d, tmp_path); n = len(store._client.files); d.records.refresh()
    assert capture_one(ITEM, d, tmp_path) == "captured" and len(store._client.files) == n

def test_capture_dry_tags_skips_merge_tags(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_DRY_TAGS", "1")
    d, store = deps(tmp_path, FetchResult(platform="youtube", text="conteudo", method="captions"))
    assert capture_one(ITEM, d, tmp_path) == "captured"
    assert d.raindrop.puts == []
