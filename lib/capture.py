"""Captura por item: fetch → raw imutável (+frames) → registros → tag espelho."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from lib.dropbox_store import ConflictError
from lib.fetch_types import FetchResult
from lib.fetcher import fetch, route
from lib.queue import is_nowiki, retry_number
from lib.rawwriter import raw_relpath, frames_reldir, render_raw


@dataclass
class Deps:
    cfg: object; raindrop: object; store: object; records: object; journal: object
    fetch_fn: Callable = fetch
    secrets: dict = field(default_factory=dict)
    now: Callable = datetime.now


def _tag(deps, item_id, add, remove, note=None):
    if os.environ.get("SR_DRY_TAGS"):
        return
    try: deps.raindrop.merge_tags(item_id, add, remove, note)
    except Exception as e: deps.journal.upsert_item(item_id, last_error=f"tag: {e}")


def capture_one(item: dict, deps: Deps, work: Path) -> str:
    iid = item["id"]; cfg = deps.cfg
    fresh = deps.raindrop.get(iid) or item
    if is_nowiki(fresh):
        deps.records.write(iid, "nowiki", {"link": item["link"]}); deps.journal.upsert_item(iid, state="nowiki"); return "nowiki"
    kinds = deps.records.kinds(iid)
    if "meta" not in kinds:
        deps.records.write(iid, "meta", {"link": item["link"], "title": item.get("title"), "created": item.get("created"),
                                         "type": item.get("type"), "tags": item.get("tags"), "raindrop": item.get("raw")})
    if "captured" in kinds:
        deps.journal.upsert_item(iid, state="captured"); return "captured"
    plat = route(item["link"], item.get("type", "link"))
    fr: FetchResult = deps.fetch_fn(item, Path(work) / f"r{iid}", cfg, deps.secrets, deps.journal)
    retries = retry_number(kinds)
    if fr.method == "none" and retries < cfg.retry_max:
        deps.records.write(iid, f"retry{retries + 1}", {"error": fr.error, "platform": plat})
        deps.journal.upsert_item(iid, state="retry", retries=retries + 1, last_error=fr.error)
        _tag(deps, iid, ["sr:retry"], []); return "retry"
    raw_rel = raw_relpath(item, plat, cfg.tz)
    frame_names = [n for n, _ in fr.frames][: cfg.frames_max]
    md = render_raw(item, fr, plat, raw_rel, frame_names).encode("utf-8")
    try:
        deps.store.add(cfg.vault_path(raw_rel), md)
    except ConflictError:
        data, _ = deps.store.download(cfg.vault_path(raw_rel))
        if not re.search(rf"^raindrop_id: {iid}$", data.decode("utf-8", "ignore"), re.M):
            deps.records.write(iid, "erro", {"error": f"raw existe com outro id: {raw_rel}"})
            deps.journal.upsert_item(iid, state="erro", last_error="raw colide"); _tag(deps, iid, ["sr:erro"], ["sr:raw", "sr:retry"]); return "erro"
    for name, blob in fr.frames[: cfg.frames_max]:
        try: deps.store.add(cfg.vault_path(f"{frames_reldir(raw_rel)}/{name}"), blob)
        except ConflictError: pass
    deps.records.write(iid, "captured", {"raw": raw_rel, "frames": frame_names, "method": fr.method, "platform": plat,
                                         "error": fr.error, "duration_s": fr.duration_s})
    deps.journal.upsert_item(iid, state="captured", raw_path=raw_rel, link=item["link"], title=item.get("title"), created=item.get("created"))
    _tag(deps, iid, ["sr:raw"], ["sr:retry"])
    return "captured"
