"""Config = config.json + variáveis de ambiente (env vence). Segredos só vêm do env."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping

REQUIRED = ["RAINDROP_TOKEN", "DROPBOX_REFRESH_TOKEN", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "GROQ_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
OPTIONAL = ["APIFY_TOKEN", "JINA_API_KEY", "FIRECRAWL_API_KEY", "UPTIMEROBOT_URL"]
INT_KEYS = {"poll_seconds", "batch_min_items", "batch_max_wait_min", "batch_max_items", "max_batches_per_day",
            "max_files_per_batch", "max_delta_bytes", "max_images_per_batch", "frames_max", "frames_short_max",
            "media_max_mb", "media_max_min", "pdf_max_pages", "queue_max", "groq_daily_seconds", "claude_timeout_s",
            "fetch_timeout_s", "retry_max"}

@dataclass
class Config:
    vault_root: str = "/Projects/vault"
    curated_dir: str = "raw/curated"
    catalog_dir: str = "raw/curated/_catalog"
    state_dir: str = "raw/curated/_state"
    wiki_dir: str = "wiki"
    log_curated_fmt: str = "wiki/log/{ym}_curated.md"
    catalog_page: str = "wiki/ferramentas-e-ideias.md"
    work_dir: str = "/var/lib/sync-raindrop"
    tz: str = "America/Sao_Paulo"
    poll_seconds: int = 30
    batch_min_items: int = 5
    batch_max_wait_min: int = 15
    batch_max_items: int = 5
    max_batches_per_day: int = 12
    max_files_per_batch: int = 10
    max_delta_bytes: int = 8192
    max_images_per_batch: int = 30
    frames_max: int = 6
    frames_short_max: int = 3
    media_max_mb: int = 200
    media_max_min: int = 30
    pdf_max_pages: int = 60
    queue_max: int = 200
    groq_daily_seconds: int = 28800
    claude_model: str = "claude-fable-5-1"
    claude_timeout_s: int = 900
    fetch_timeout_s: int = 600
    retry_max: int = 3
    types_vocab: list = field(default_factory=lambda: ["skill", "plugin", "ferramenta", "metodo", "ideia", "conteudo"])
    triggers_vocab_path: str = "config/triggers.txt"
    # segredos
    raindrop_token: str = ""
    dropbox_refresh_token: str = ""
    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    groq_api_key: str = ""
    claude_oauth_token: str = ""
    apify_token: str | None = None
    jina_api_key: str | None = None
    firecrawl_api_key: str | None = None
    uptimerobot_url: str | None = None

    def vault_path(self, rel: str) -> str:
        """Caminho absoluto no Dropbox (sempre começa com /)."""
        return f"{self.vault_root.rstrip('/')}/{rel.lstrip('/')}"

def load_config(path: Path, env: Mapping[str, str]) -> Config:
    data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    known = {f.name for f in fields(Config)}
    kwargs = {k: v for k, v in data.items() if k in known}

    # env overrides for INT_KEYS
    for k in INT_KEYS:
        if k.upper() in env:
            kwargs[k] = int(env[k.upper()])

    # env overrides for string keys (vault_root, work_dir)
    if "VAULT_ROOT" in env:
        kwargs["vault_root"] = env["VAULT_ROOT"]
    if "WORK_DIR" in env:
        kwargs["work_dir"] = env["WORK_DIR"]

    # env override for claude_model
    if "CLAUDE_MODEL" in env:
        kwargs["claude_model"] = env["CLAUDE_MODEL"]

    # check for required secrets
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise ValueError(f"faltam variáveis: {', '.join(missing)}")

    # set required secrets
    kwargs.update(raindrop_token=env["RAINDROP_TOKEN"], dropbox_refresh_token=env["DROPBOX_REFRESH_TOKEN"],
                  dropbox_app_key=env["DROPBOX_APP_KEY"], dropbox_app_secret=env["DROPBOX_APP_SECRET"],
                  groq_api_key=env["GROQ_API_KEY"], claude_oauth_token=env["CLAUDE_CODE_OAUTH_TOKEN"])

    # set optional secrets
    for k in OPTIONAL:
        kwargs[k.lower()] = env.get(k) or None

    return Config(**kwargs)
