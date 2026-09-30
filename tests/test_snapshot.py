from lib.snapshot import Snapshot, UndoRepo, Snap
from lib.config import Config
from tests.test_dropbox_store import make as make_store


def test_pull_reads_wiki_and_claude_md():
    store = make_store(); cfg = Config()
    store.add("/Projects/vault/wiki/a.md", b"# A"); store.add("/Projects/vault/wiki/log/2026-09_curated.md", b"log"); store.add("/Projects/vault/CLAUDE.md", b"regras")
    store.add("/Projects/vault/wiki/img.png", b"\x89PNG")
    snap = Snapshot(store, cfg).pull()
    assert set(snap.files) == {"wiki/a.md", "wiki/log/2026-09_curated.md", "CLAUDE.md"}
    assert snap.files["wiki/a.md"][0] == b"# A" and snap.files["wiki/a.md"][1]


def test_pull_skips_contatos_and_does_not_count_them():
    """wiki/contatos/ são milhares de páginas automáticas (ecosystem-knowledge): não entram no snapshot
    nem contam para o MAX_FILES — senão todo lote aborta (2026-09-30, 4.685 .md > 300)."""
    store = make_store(); cfg = Config()
    store.add("/Projects/vault/wiki/a.md", b"# A"); store.add("/Projects/vault/CLAUDE.md", b"regras")
    for i in range(Snapshot.MAX_FILES + 5):
        store.add(f"/Projects/vault/wiki/contatos/c{i}.md", b"auto")
    snap = Snapshot(store, cfg).pull()
    assert set(snap.files) == {"wiki/a.md", "CLAUDE.md"}


def test_pull_fails_loud_without_claude_md():
    """Sem vault/CLAUDE.md o destilador rodava sem as regras da wiki e ninguém via (erro engolido até 2026-09-30).
    Agora o lote aborta com mensagem clara; 3 seguidos derrubam o health (lotes_abortando)."""
    import pytest
    store = make_store(); cfg = Config()
    store.add("/Projects/vault/wiki/a.md", b"# A")
    with pytest.raises(RuntimeError, match="CLAUDE.md"):
        Snapshot(store, cfg).pull()


def test_undo_repo_commits(tmp_path):
    repo = UndoRepo(tmp_path / "wiki-git")
    sha1 = repo.commit_snapshot(Snap({"wiki/a.md": (b"v1", "r1")}), "pull run1")
    sha2 = repo.commit_snapshot(Snap({"wiki/a.md": (b"v2", "r2")}), "push run1")
    assert sha1 and sha2 and sha1 != sha2
    assert repo.show(sha1, "wiki/a.md") == b"v1" and repo.show(sha2, "wiki/a.md") == b"v2"
    assert repo.commit_snapshot(Snap({"wiki/a.md": (b"v2", "r2")}), "nada") == ""


def test_undo_repo_drops_deleted_files(tmp_path):
    repo = UndoRepo(tmp_path / "wiki-git")
    sha1 = repo.commit_snapshot(Snap({"wiki/a.md": (b"a", "r1"), "wiki/b.md": (b"b", "r1")}), "add two files")
    sha2 = repo.commit_snapshot(Snap({"wiki/a.md": (b"a", "r1")}), "delete b")
    assert sha1 and sha2 and sha1 != sha2
    assert repo.show(sha1, "wiki/b.md") == b"b"
    assert repo.show(sha2, "wiki/b.md") is None
