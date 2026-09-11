"""Raw imutável: nome determinístico + frontmatter + conteúdo + JSON completo do Raindrop."""
from __future__ import annotations
import json, re, unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
from lib.fetch_types import FetchResult

CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f​-‏  ﻿]")

def sanitize_control_chars(s: str) -> str:
    return CTRL.sub("", s or "")

def slugify(title: str, fallback: str, max_len: int = 60) -> str:
    s = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:max_len].rstrip("-")
    return s or fallback

def raw_relpath(item: dict, platform: str, tz: str) -> str:
    created = datetime.fromisoformat(item["created"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
    return f"raw/curated/{created:%Y-%m-%d}_{platform}-{slugify(item.get('title') or '', 'sem-titulo')}-r{item['id']}.md"

def frames_reldir(raw_rel: str) -> str:
    return raw_rel[:-3] + "/frames"

def _y(s) -> str:
    return json.dumps(sanitize_control_chars(str(s)), ensure_ascii=False)

def render_raw(item: dict, fr: FetchResult, platform: str, raw_rel: str, frame_names: list[str], origem: str = "raindrop") -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = ["---", f"source: {_y(item['link'])}", f"platform: {_y(platform)}", f"raindrop_id: {item['id']}",
             f"title: {_y(fr.title or item.get('title') or '')}", f"author: {_y(fr.author or '')}",
             f"created: {_y(item['created'])}", f"captured_at: {_y(now)}", f"transcript_method: {_y(fr.method)}",
             f"duration_s: {int(fr.duration_s) if fr.duration_s else 0}", "tags:"] + [f"  - {_y(t)}" for t in item.get("tags", [])]
    lines += [f"note: {_y(item.get('note') or '')}"]
    if frame_names:
        lines += ["frames:"] + [f"  - {n}" for n in frame_names]
    else:
        lines += ["frames: []"]
    lines += [f"origem: {_y(origem)}", "ingested: true", "---", ""]
    body = [f"# {sanitize_control_chars(fr.title or item.get('title') or item['link'])}", ""]
    if item.get("note"): body += ["## Nota do Lucas", sanitize_control_chars(item["note"]), ""]
    if item.get("excerpt"): body += ["## Resumo do Raindrop", sanitize_control_chars(item["excerpt"]), ""]
    body += ["## Conteúdo", sanitize_control_chars(fr.text) if fr.text else f"_(sem conteúdo: {fr.error or fr.method})_", ""]
    body += ["## Raindrop (JSON)", "```json", json.dumps(item.get("raw", {}), ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines + body)
