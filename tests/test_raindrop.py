import json
from pathlib import Path
from lib.raindrop import RaindropClient, normalize

PAGE = json.loads((Path(__file__).parent / "fixtures" / "raindrop_page.json").read_text())

def fake_http_factory(log):
    def http(method, url, body=None):
        log.append((method, url, body))
        if method == "GET" and "/raindrops/0" in url:
            return (200, PAGE if "page=0" in url else {"result": True, "items": []})
        if method == "GET" and "/raindrop/1001" in url:
            return (200, {"result": True, "item": PAGE["items"][0]})
        if method == "PUT":
            return (200, {"result": True, "item": {**PAGE["items"][0], **body}})
        return (404, {})
    return http

def test_normalize_keeps_full_raw():
    it = normalize(PAGE["items"][0])
    assert it["id"] == 1001 and it["domain"] == "youtube.com" and it["raw"]["_id"] == 1001
    assert it["created"] == "2026-09-10T12:00:00.000Z"

def test_list_all_paginates_and_stops(monkeypatch):
    log = []; c = RaindropClient("t", http=fake_http_factory(log))
    monkeypatch.setattr("lib.raindrop.time.sleep", lambda s: None)
    items = c.list_all()
    assert [i["id"] for i in items] == [1001, 1000]
    assert any("page=1" in u for _, u, _ in log)          # pediu a 2ª página e parou na vazia

def test_list_all_since_cuts():
    c = RaindropClient("t", http=fake_http_factory([]))
    items = c.list_all(since_iso="2026-09-10T00:00:00Z")
    assert [i["id"] for i in items] == [1001]

def test_merge_tags_only_touches_sr():
    log = []; c = RaindropClient("t", http=fake_http_factory(log))
    c.merge_tags(1001, add=["sr:raw"], remove=["sr:retry"], note_suffix="→ wiki: [[a]]")
    put = [b for m, u, b in log if m == "PUT"][0]
    assert put["tags"] == ["dev", "sr:raw"]                 # manteve "dev", não duplicou
    assert put["note"].endswith("→ wiki: [[a]]")
