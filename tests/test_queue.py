from lib.queue import pending, needs_capture, needs_distill, retry_number, is_nowiki


def it(i, created, tags=()):
    return {"id": i, "created": created, "tags": list(tags), "link": "https://x", "title": "", "excerpt": "", "note": "", "type": "link", "cover": "", "domain": "x", "raw": {}}


def test_pending_filters_and_orders():
    items = [it(1, "2026-09-01T00:00:00Z"), it(2, "2026-09-03T00:00:00Z", ["nowiki"]), it(3, "2026-09-02T00:00:00Z"), it(4, "2026-09-04T00:00:00Z")]
    recs = {3: {"meta", "wiki"}, 4: {"meta", "captured"}}
    q, full = pending(items, recs, queue_max=200)
    assert [x["id"] for x in q] == [4, 1] and full is False        # 2=nowiki, 3=terminal; ordem created desc


def test_queue_max_flag():
    items = [it(i, f"2026-09-{i:02d}T00:00:00Z") for i in range(1, 6)]
    q, full = pending(items, {}, queue_max=3)
    assert len(q) == 3 and full is True


def test_predicates():
    assert needs_capture(it(1, ""), set()) and not needs_capture(it(1, ""), {"captured"})
    assert needs_distill({"captured"}) and not needs_distill({"captured", "distill"}) and not needs_distill({"captured", "wiki"})
    assert retry_number({"meta", "retry1", "retry2"}) == 2
    assert is_nowiki(it(1, "", ["NoWiki"]))
