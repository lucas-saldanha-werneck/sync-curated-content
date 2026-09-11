"""Cliente mínimo do Raindrop.io (REST v1). Só lê e mescla tags/nota próprias (sr:*)."""
from __future__ import annotations
import json, time, urllib.error, urllib.request
from typing import Callable

API = "https://api.raindrop.io/rest/v1"
Item = dict

def normalize(raw: dict) -> Item:
    return {"id": int(raw["_id"]), "link": raw.get("link", ""), "title": raw.get("title", "") or "",
            "excerpt": raw.get("excerpt", "") or "", "note": raw.get("note", "") or "", "tags": list(raw.get("tags") or []),
            "type": raw.get("type", "link"), "created": raw.get("created", ""), "cover": raw.get("cover", "") or "",
            "domain": raw.get("domain", "") or "", "raw": raw}

def _urllib_http(token: str):
    def http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            reset = e.headers.get("X-RateLimit-Reset") if e.code == 429 else None
            return e.code, {"error": str(e), "reset": reset}
    return http

class RaindropClient:
    def __init__(self, token: str, http: Callable | None = None):
        self.http = http or _urllib_http(token)

    def _call(self, method, url, body=None) -> dict:
        status, data = self.http(method, url, body)
        if status == 429:
            wait = 60
            try: wait = max(1, min(60, int((data if isinstance(data, dict) else {}).get("reset") or 0) - int(time.time())))
            except (TypeError, ValueError, AttributeError): pass
            time.sleep(wait); status, data = self.http(method, url, body)
        if status >= 400:
            raise RuntimeError(f"raindrop {method} {url} → {status} {data}")
        return data

    def list_all(self, since_iso: str | None = None, perpage: int = 50, max_pages: int = 40) -> list[Item]:
        out: list[Item] = []
        for page in range(max_pages):
            data = self._call("GET", f"{API}/raindrops/0?sort=-created&perpage={perpage}&page={page}")
            items = [normalize(r) for r in data.get("items", [])]
            if not items: break
            for it in items:
                if since_iso and it["created"] < since_iso:
                    return out
                out.append(it)
            time.sleep(0.6)
        return out

    def get(self, item_id: int) -> Item | None:
        data = self._call("GET", f"{API}/raindrop/{item_id}")
        return normalize(data["item"]) if data.get("item") else None

    def merge_tags(self, item_id: int, add: list[str], remove: list[str], note_suffix: str | None = None) -> Item:
        cur = self.get(item_id)
        if cur is None: raise RuntimeError(f"raindrop {item_id} sumiu")
        tags = [t for t in cur["tags"] if not (t.startswith("sr:") and t in remove)]
        for t in add:
            if t not in tags: tags.append(t)
        body = {"tags": tags}
        if note_suffix and note_suffix not in cur["note"]:
            body["note"] = (cur["note"] + "\n" if cur["note"] else "") + note_suffix
        data = self._call("PUT", f"{API}/raindrop/{item_id}", body)
        return normalize(data["item"])
