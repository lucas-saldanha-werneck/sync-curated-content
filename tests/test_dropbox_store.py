import pytest
from lib.dropbox_store import DropboxStore, ConflictError, content_hash, RcloneTokenProvider

class FakeMeta:
    def __init__(self, rev, ch): self.rev, self.content_hash = rev, ch
class FakeErr(Exception):
    def __init__(self, kind): self.kind = kind
class FakeDbx:
    """Simula files_upload com WriteMode add/update(rev)+strict_conflict."""
    def __init__(self, token): self.token = token; self.files = {}; self.n = 0
    def _rev(self): self.n += 1; return f"rev{self.n}"
    def files_upload(self, data, path, mode=None, strict_conflict=False, content_hash=None):
        tag = mode._tag
        cur = self.files.get(path)
        if tag == "add" and cur is not None: raise FakeErr("conflict")
        if tag == "update":
            if cur is None or cur[1] != mode.get_update(): raise FakeErr("conflict")
        r = self._rev(); self.files[path] = (data, r); return FakeMeta(r, content_hash(data))
    def files_download(self, path):
        if path not in self.files: raise FakeErr("not_found")
        data, r = self.files[path]
        class R: content = data
        return FakeMeta(r, content_hash(data)), R()
    def files_get_metadata(self, path):
        if path not in self.files: raise FakeErr("not_found")
        return FakeMeta(self.files[path][1], content_hash(self.files[path][0]))
    def files_list_folder(self, folder, recursive=True):
        class E:
            def __init__(s, p, d, r): s.path_display, s.rev, s.content_hash, s.size = p, r, content_hash(d), len(d)
        ents = [E(p, d, r) for p, (d, r) in self.files.items() if p.startswith(folder.rstrip("/") + "/")]
        class Res: entries = ents; has_more = False; cursor = ""
        return Res()

class Prov:
    def __init__(self): self.t = "tok"; self.refreshed = 0
    def token(self): return self.t
    def refresh(self): self.refreshed += 1; self.t = "tok2"; return self.t

class WM:
    class _M:
        def __init__(self, tag, rev=None): self._tag, self._rev = tag, rev
        def get_update(self): return self._rev
    add = _M("add")
    @staticmethod
    def update(rev): return WM._M("update", rev)

def make():
    return DropboxStore(Prov(), client_factory=FakeDbx, is_conflict=lambda e: getattr(e, "kind", "") == "conflict",
                        is_not_found=lambda e: getattr(e, "kind", "") == "not_found", is_auth=lambda e: False,
                        write_mode=lambda: WM)

def test_add_then_update_cas():
    s = make()
    r1 = s.add("/v/a.md", b"one")
    r2 = s.update("/v/a.md", b"two", r1)
    assert r2 != r1
    with pytest.raises(ConflictError):
        s.update("/v/a.md", b"three", r1)          # rev velho
    data, rev = s.download("/v/a.md"); assert data == b"two" and rev == r2

def test_add_idempotent_same_content_conflict_if_different():
    s = make()
    r1 = s.add("/v/b.md", b"x")
    assert s.add("/v/b.md", b"x") == r1            # idêntico → mesmo rev
    with pytest.raises(ConflictError):
        s.add("/v/b.md", b"y")

def test_exists_and_hash():
    s = make(); s.add("/v/c.md", b"abc")
    ok, rev, ch = s.exists("/v/c.md"); assert ok and rev and ch == content_hash(b"abc")
    assert s.exists("/v/nope.md") == (False, None, None)

def test_content_hash_known_value():
    assert content_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

def test_rclone_provider_reads_and_refreshes(tmp_path):
    conf = tmp_path / "rclone.conf"
    conf.write_text('[dropbox]\ntype = dropbox\ntoken = {"access_token":"A1","refresh_token":"R","expiry":"2099-01-01T00:00:00Z"}\n')
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd); conf.write_text(conf.read_text().replace("A1", "A2")); return type("P", (), {"returncode": 0})()
    p = RcloneTokenProvider(conf, runner=runner)
    assert p.token() == "A1"
    assert p.refresh() == "A2" and calls and "lsd" in calls[0]

def test_list_folder():
    s = make()
    s.add("/v/a.md", b"one")
    s.add("/v/b.md", b"x")
    got = s.list("/v")
    paths = {e["path"] for e in got}
    assert paths == {"/v/a.md", "/v/b.md"}
    for e in got:
        assert set(e.keys()) == {"path", "rev", "content_hash", "size"}
    assert s.list("/nope") == []
