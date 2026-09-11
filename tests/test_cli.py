import sys, json
from pathlib import Path
import importlib

def test_once_cycle_captures_and_reports(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
    import sync as cli
    from tests.test_capture import FakeRD
    from tests.test_dropbox_store import make as make_store
    from lib.config import Config
    from lib.records import Records
    from lib.journal import Journal
    from lib.fetch_types import FetchResult
    cfg = Config(work_dir=str(tmp_path)); store = make_store(); rec = Records(store, cfg)
    items = [{"id": 3, "link": "https://blog.com/x", "title": "X", "excerpt": "", "note": "", "tags": [], "type": "article",
              "created": "2026-09-10T10:00:00.000Z", "cover": "", "domain": "blog.com", "raw": {"_id": 3}}]
    class RD(FakeRD):
        def list_all(self, since_iso=None, **k): return items
    rd = RD(items[0])
    fetch_fn = lambda it, work, cfg, secrets, journal: FetchResult(platform="article", text="t" * 500, method="trafilatura")
    monkeypatch.setattr(cli, "make_raindrop", lambda cfg: rd)
    monkeypatch.setattr(cli, "make_store", lambda cfg, env: store)
    monkeypatch.setattr(cli, "make_fetch_fn", lambda: fetch_fn)
    rc = cli.main(["once", "--config", str(Path(__file__).resolve().parents[1] / "config.json")], env={"RAINDROP_TOKEN": "t", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s", "GROQ_API_KEY": "g", "CLAUDE_CODE_OAUTH_TOKEN": "c", "WORK_DIR": str(tmp_path), "HEALTH_PUBLIC_DIR": str(tmp_path / "public")})
    assert rc == 0
    rec.refresh(); assert "captured" in rec.kinds(3)
    assert (tmp_path / "health.json").exists()
