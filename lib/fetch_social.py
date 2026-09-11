"""Redes sem API pública: Instagram/LinkedIn via Apify; X via fxtwitter."""
from __future__ import annotations
import json, re, urllib.parse, urllib.request
from pathlib import Path
from lib.fetch_types import FetchResult
from lib.net_guard import safe_open

APIFY = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}&timeout={t}"

def apify_run_sync(actor: str, token: str, payload: dict, opener=None, timeout: int = 300) -> list[dict]:
    url = APIFY.format(actor=actor, token=urllib.parse.quote(token), t=timeout)
    if opener:                                            # opener aqui é só para teste; não envia payload
        body, _, _ = opener(url, timeout=timeout + 30, headers={"Content-Type": "application/json"})
        return json.loads(body.decode("utf-8", "ignore"))
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

def fetch_instagram(url, work, cfg, apify_token, groq_key, opener=None, video_fn=None) -> FetchResult:
    if not apify_token:
        return FetchResult(platform="instagram", method="none", error="apify: sem token")
    try:
        items = apify_run_sync("apify~instagram-scraper", apify_token, {"directUrls": [url], "resultsLimit": 1}, opener=opener)
    except Exception as e:
        return FetchResult(platform="instagram", method="none", error=f"apify: {e}")
    if not isinstance(items, list) or not items:
        return FetchResult(platform="instagram", method="none", error=f"apify: resposta inválida: {str(items)[:120]}")
    it = items[0]; parts = []
    if it.get("caption"): parts.append("Legenda: " + it["caption"])
    r = FetchResult(platform="instagram", method="apify", author=it.get("ownerUsername"), title=(it.get("caption") or "")[:80] or None)
    if it.get("videoUrl"):
        try:
            from lib.fetch_video import fetch_video
            v = (video_fn or fetch_video)(it["videoUrl"], Path(work) / "ig", cfg, groq_key, "instagram")
            if v.text: parts.append("Transcrição: " + v.text)
            r.duration_s, r.frames = v.duration_s, v.frames
            r.extra["video_method"] = v.method
        except Exception as e:
            r.extra["video_error"] = str(e)
    r.text = "\n\n".join(parts)
    return r

def fetch_linkedin(url, cfg, apify_token, opener=None, actor: str = "apimaestro~linkedin-post-detail") -> FetchResult:
    if not apify_token:
        return FetchResult(platform="linkedin", method="none", error="apify: sem token")
    try:
        items = apify_run_sync(actor, apify_token, {"postUrl": url}, opener=opener)
    except Exception as e:
        return FetchResult(platform="linkedin", method="none", error=f"apify: {e}")
    if not isinstance(items, list) or not items:
        return FetchResult(platform="linkedin", method="none", error=f"apify: resposta inválida: {str(items)[:120]}")
    it = items[0]
    text = it.get("text") or it.get("postText") or it.get("content") or ""
    author = it.get("author"); author = author.get("name") if isinstance(author, dict) else it.get("authorName")
    return FetchResult(platform="linkedin", method="apify", text=text, author=author)

def fetch_x(url, cfg, opener=safe_open) -> FetchResult:
    m = re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
    if not m:
        return FetchResult(platform="x", method="none", error="url de tweet não reconhecida")
    try:
        body, _, _ = opener(f"https://api.fxtwitter.com/{m.group(1)}/status/{m.group(2)}", timeout=30)
        tw = json.loads(body.decode("utf-8", "ignore")).get("tweet") or {}
    except Exception as e:
        return FetchResult(platform="x", method="none", error=f"fxtwitter: {e}")
    text = tw.get("text") or ""
    if (tw.get("quote") or {}).get("text"): text += "\n\n> " + tw["quote"]["text"]
    r = FetchResult(platform="x", method="fxtwitter", text=text, author=(tw.get("author") or {}).get("screen_name"))
    vids = ((tw.get("media") or {}).get("videos") or [])
    if vids: r.extra["video_url"] = vids[0].get("url")
    return r
