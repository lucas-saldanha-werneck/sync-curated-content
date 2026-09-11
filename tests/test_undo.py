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

def test_undo_two_ops_same_path(tmp_path):
    """Two ops on same path: one result entry, stripped of both ids."""
    store = make_store(); cfg = Config(); j = Journal(tmp_path / "j.db")
    r0 = store.add("/Projects/vault/wiki/a.md", b"a\n## X\nb\n")
    after1 = ("a\n## X\n" + marker(7, "2026-09-11") + "\n- c1\nb\n").encode()
    r1 = store.update("/Projects/vault/wiki/a.md", after1, r0)
    after2 = ("a\n## X\n" + marker(7, "2026-09-11") + "\n- c1\n" + marker(8, "2026-09-11") + "\n- c2\nb\n").encode()
    r2 = store.update("/Projects/vault/wiki/a.md", after2, r1)
    rid = j.start_run("distill", [7, 8])
    op1 = j.add_op(rid, 7, "wiki/a.md", "update", r0, "s1"); j.confirm_op(op1, r1)
    op2 = j.add_op(rid, 8, "wiki/a.md", "update", r1, "s2"); j.confirm_op(op2, r2); j.finish_run(rid, "ok", {})
    res = undo_run(rid, store, j, cfg)
    assert len(res["reverted"]) == 1 and res["reverted"][0] == "wiki/a.md"
    assert store.download("/Projects/vault/wiki/a.md")[0] == b"a\n## X\nb\n"

def test_strip_supersede_with_parens():
    """Parens in supersede note don't break regex."""
    t = "old ~~velho~~ (superado em 2026-09-11, ver r1: nota (com parenteses) aqui)\n"
    assert strip_markers(t, [1]) == "old velho\n"

def test_strip_catalog_row_and_log_line():
    """Catalog row and log line with marker are removed."""
    t = "- item <!-- r7 -->\n- 2026-09-11 r7 action\ntext\n"
    assert strip_markers(t, [7]) == "text\n"

def test_undo_unchanged_not_written(tmp_path):
    """If stripping changes nothing, don't write, report in unchanged."""
    store = make_store(); cfg = Config(); j = Journal(tmp_path / "j.db")
    r0 = store.add("/Projects/vault/wiki/a.md", b"a\n## X\nb\n")
    r1 = store.update("/Projects/vault/wiki/a.md", b"a\n## X\nb\n", r0)
    rid = j.start_run("distill", [999]); op = j.add_op(rid, 999, "wiki/a.md", "update", r0, "s"); j.confirm_op(op, r1)
    res = undo_run(rid, store, j, cfg)
    assert res["unchanged"] == ["wiki/a.md"] and res["reverted"] == [] and res["skipped"] == []
    assert store.download("/Projects/vault/wiki/a.md")[0] == b"a\n## X\nb\n"


def test_undo_via_apply_strips_every_item_of_the_op(tmp_path):
    # M1 (review Opus): apply() gravava 1 linha de op por arquivo com item_ids[0]; undo tirava só o 1º item do lote
    from lib.applier import Manifest, Op, apply
    store = make_store(); cfg = Config(); j = Journal(tmp_path / "j.db")
    before = b"a\n## X\nb\n"; r0 = store.add("/Projects/vault/wiki/a.md", before)
    after = ("a\n## X\n" + marker(7, "2026-09-11") + "\n- c7\n" + marker(8, "2026-09-11") + "\n- c8\nb\n").encode()
    rid = j.start_run("distill", [7, 8])
    m = Manifest(run_id=rid, ops=[Op(path="wiki/a.md", kind="update", before=before, after=after, rev_before=r0, item_ids=[7, 8], delta_sha="s")])
    apply(m, store, j, cfg); j.finish_run(rid, "ok", {})
    assert sorted(op["item_id"] for op in j.ops(rid)) == [7, 8]
    res = undo_run(rid, store, j, cfg)
    assert res["reverted"] == ["wiki/a.md"] and store.download("/Projects/vault/wiki/a.md")[0] == before


def test_undo_of_new_page_deletes_file(tmp_path):
    # Codex #4: undo de página criada pelo lote deve APAGAR o arquivo (não deixar stub que bloqueia o nome pra sempre)
    from lib.applier import Manifest, Op, apply
    store = make_store(); cfg = Config(); j = Journal(tmp_path / "j.db")
    body = ("# nova\n\nresumo\n\n" + marker(7, "2026-09-11") + "\ncorpo\n").encode()
    rid = j.start_run("distill", [7])
    m = Manifest(run_id=rid, ops=[Op(path="wiki/nova.md", kind="add", before=None, after=body, rev_before=None, item_ids=[7], delta_sha="s")])
    apply(m, store, j, cfg); j.finish_run(rid, "ok", {})
    assert store.exists("/Projects/vault/wiki/nova.md")[0]
    res = undo_run(rid, store, j, cfg)
    assert res["reverted"] == ["wiki/nova.md"]
    assert store.exists("/Projects/vault/wiki/nova.md")[0] is False            # apagado de verdade
