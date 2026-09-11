from pathlib import Path
import json
from lib.config import load_config

def test_load_config_merges_env(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"poll_seconds": 30, "claude_model": "claude-fable-5-1", "types_vocab": ["skill"]}))
    env = {"RAINDROP_TOKEN": "rt", "DROPBOX_REFRESH_TOKEN": "dr", "DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s",
           "GROQ_API_KEY": "g", "CLAUDE_CODE_OAUTH_TOKEN": "c", "POLL_SECONDS": "45"}
    cfg = load_config(cfg_path, env)
    assert cfg.poll_seconds == 45            # env vence o json
    assert cfg.claude_model == "claude-fable-5-1"
    assert cfg.raindrop_token == "rt" and cfg.groq_api_key == "g"
    assert cfg.apify_token is None           # opcional

def test_missing_required_secret_raises(tmp_path):
    cfg_path = tmp_path / "config.json"; cfg_path.write_text("{}")
    import pytest
    with pytest.raises(ValueError, match="RAINDROP_TOKEN"):
        load_config(cfg_path, {})

def test_load_config_vault_root_from_env(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"vault_root": "/Projects/vault"}))
    env = {"RAINDROP_TOKEN": "rt", "DROPBOX_REFRESH_TOKEN": "dr", "DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s",
           "GROQ_API_KEY": "g", "CLAUDE_CODE_OAUTH_TOKEN": "c", "VAULT_ROOT": "/Projects/vault-sandbox"}
    cfg = load_config(cfg_path, env)
    assert cfg.vault_root == "/Projects/vault-sandbox"

def test_load_config_work_dir_from_env(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"work_dir": "/var/lib/sync-raindrop"}))
    env = {"RAINDROP_TOKEN": "rt", "DROPBOX_REFRESH_TOKEN": "dr", "DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s",
           "GROQ_API_KEY": "g", "CLAUDE_CODE_OAUTH_TOKEN": "c", "WORK_DIR": "/tmp/work-dir"}
    cfg = load_config(cfg_path, env)
    assert cfg.work_dir == "/tmp/work-dir"
