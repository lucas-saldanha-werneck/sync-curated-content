from lib.undo import strip_markers, undo_run
from lib.applier import marker
from lib.journal import Journal
from lib.config import Config
from tests.test_dropbox_store import make as make_store

def test_strip_markers_removes_block_and_supersede():
    t = "a\n## X\n" + marker(1, "2026-09-11") + "\n- claim ([raw](../r))\nb ~~velho~~ (superado em 2026-09-11, ver r1: n)\n"
    assert strip_markers(t, [1]) == "a\n## X\nb velho\n"

def test_undo_reverts_when_rev_matches(tmp_path):
    store = make_store(); cfg = Config(); j = Journal(tmp_path / "j.db")
    r0 = store.add("/Projects/vault/wiki/a.md", b"a\n## X\nb\n")
    after = ("a\n## X\n" + marker(7, "2026-09-11") + "\n- c\nb\n").encode()
    r1 = store.update("/Projects/vault/wiki/a.md", after, r0)
    rid = j.start_run("distill", [7]); op = j.add_op(rid, 7, "wiki/a.md", "update", r0, "sha"); j.confirm_op(op, r1); j.finish_run(rid, "ok", {})
    res = undo_run(rid, store, j, cfg)
    assert res["reverted"] == ["wiki/a.md"] and store.download("/Projects/vault/wiki/a.md")[0] == b"a\n## X\nb\n"

def test_undo_skips_when_edited_later(tmp_path):
    store = make_store(); cfg = Config(); j = Journal(tmp_path / "j.db")
    r0 = store.add("/Projects/vault/wiki/a.md", b"x\n"); r1 = store.update("/Projects/vault/wiki/a.md", b"y\n", r0)
    store.update("/Projects/vault/wiki/a.md", b"z\n", r1)                     # Mac editou depois
    rid = j.start_run("distill", [7]); op = j.add_op(rid, 7, "wiki/a.md", "update", r0, "s"); j.confirm_op(op, r1)
    res = undo_run(rid, store, j, cfg)
    assert res["skipped"] == ["wiki/a.md"] and store.download("/Projects/vault/wiki/a.md")[0] == b"z\n"
