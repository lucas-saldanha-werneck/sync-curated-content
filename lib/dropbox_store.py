"""Escrita no Dropbox com compare-and-swap. Nunca WriteMode.overwrite."""
from __future__ import annotations
import hashlib, json, re, subprocess, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

class ConflictError(Exception): ...

def content_hash(data: bytes) -> str:
    """Dropbox content hash: sha256 de cada bloco de 4 MiB, concatena os digests, sha256 do total.
    Dados vazios têm zero blocos, então a concatenação é b"" e o hash é sha256(b"")."""
    block = 4 * 1024 * 1024; acc = b""
    for i in range(0, len(data), block):
        acc += hashlib.sha256(data[i:i + block]).digest()
    return hashlib.sha256(acc).hexdigest()

class TokenProvider:
    def token(self) -> str: raise NotImplementedError
    def refresh(self) -> str: raise NotImplementedError

class RcloneTokenProvider(TokenProvider):
    def __init__(self, conf_path: Path, remote: str = "dropbox", runner: Callable = subprocess.run):
        self.conf, self.remote, self.run = Path(conf_path), remote, runner
    def _read(self) -> dict:
        txt = self.conf.read_text(encoding="utf-8")
        m = re.search(rf"\[{self.remote}\](.*?)(?=\n\[|\Z)", txt, re.S)
        tok = re.search(r"^token\s*=\s*(\{.*\})\s*$", m.group(1), re.M)
        return json.loads(tok.group(1))
    def token(self) -> str:
        t = self._read()
        exp = t.get("expiry")
        if exp:
            try:
                dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if dt - datetime.now(timezone.utc) < timedelta(minutes=5):
                    return self.refresh()
            except ValueError:
                pass
        return t["access_token"]
    def refresh(self) -> str:
        self.run(["rclone", "--config", str(self.conf), "lsd", f"{self.remote}:"], capture_output=True, timeout=60)
        return self._read()["access_token"]

class EnvTokenProvider(TokenProvider):
    def __init__(self, refresh_token: str, app_key: str, app_secret: str):
        import dropbox
        self.dbx = dropbox.Dropbox(oauth2_refresh_token=refresh_token, app_key=app_key, app_secret=app_secret)
    def token(self) -> str:
        self.dbx.check_and_refresh_access_token(); return self.dbx._oauth2_access_token
    def refresh(self) -> str:
        self.dbx.refresh_access_token(); return self.dbx._oauth2_access_token

def _default_factory(token: str):
    import dropbox
    return dropbox.Dropbox(oauth2_access_token=token, timeout=120)

def _dbx_is_conflict(e) -> bool:
    err = getattr(e, "error", None)
    try:
        return err.is_path() and err.get_path().reason.is_conflict()
    except AttributeError:
        try: return err.is_path() and err.get_path().is_conflict()
        except AttributeError: return False

def _dbx_is_not_found(e) -> bool:
    err = getattr(e, "error", None)
    try: return err.is_path() and err.get_path().is_not_found()
    except AttributeError: return False

def _dbx_is_auth(e) -> bool:
    return e.__class__.__name__ == "AuthError"

def _dbx_is_retryable(e) -> bool:
    """Só erros transitórios: HTTP 5xx/429 do Dropbox (por nome, sem import obrigatório
    do SDK) ou falha de rede/timeout local. Nunca erros de programação (TypeError etc)."""
    if e.__class__.__name__ in ("HttpError", "InternalServerError", "RateLimitError", "URLError"):
        return True
    return isinstance(e, (OSError, ConnectionError, TimeoutError))

class DropboxStore:
    def __init__(self, provider: TokenProvider, client_factory: Callable | None = None,
                 is_conflict=_dbx_is_conflict, is_not_found=_dbx_is_not_found, is_auth=_dbx_is_auth,
                 write_mode=None, is_retryable=_dbx_is_retryable):
        self.provider = provider; self.factory = client_factory or _default_factory
        self.is_conflict, self.is_not_found, self.is_auth = is_conflict, is_not_found, is_auth
        self.is_retryable = is_retryable
        self._wm = write_mode or (lambda: __import__("dropbox.files", fromlist=["WriteMode"]).WriteMode)
        self._client = None

    def _c(self):
        if self._client is None: self._client = self.factory(self.provider.token())
        return self._client

    def _call(self, fn, *a, **kw):
        delay = 2
        for attempt in range(4):
            try:
                return fn(self._c(), *a, **kw)
            except Exception as e:
                if self.is_conflict(e): raise ConflictError(str(e)) from e
                if self.is_not_found(e): raise FileNotFoundError(str(e)) from e
                if self.is_auth(e) and attempt == 0:
                    self._client = self.factory(self.provider.refresh()); continue
                if not self.is_retryable(e): raise
                if attempt == 3: raise
                time.sleep(delay); delay *= 2

    def _modes(self):
        return self._wm()

    def add(self, path: str, data: bytes) -> str:
        ok, rev, ch = self.exists(path)
        if ok:
            if ch == content_hash(data): return rev
            raise ConflictError(f"{path} existe com conteúdo diferente")
        WM = self._modes()
        meta = self._call(lambda c: c.files_upload(data, path, mode=WM.add, strict_conflict=True, content_hash=content_hash(data)))
        return meta.rev

    def update(self, path: str, data: bytes, rev: str) -> str:
        WM = self._modes()
        meta = self._call(lambda c: c.files_upload(data, path, mode=WM.update(rev), strict_conflict=True, content_hash=content_hash(data)))
        return meta.rev

    def delete(self, path: str, rev: str) -> None:
        # apaga com CAS: só remove se o rev bater (senão ConflictError). Usado pelo undo de página nova.
        WM = self._modes()
        self._call(lambda c: c.files_delete_v2(path, parent_rev=rev))

    def download(self, path: str) -> tuple[bytes, str]:
        meta, res = self._call(lambda c: c.files_download(path))
        return res.content, meta.rev

    def exists(self, path: str):
        try:
            meta = self._call(lambda c: c.files_get_metadata(path))
        except FileNotFoundError:
            return (False, None, None)
        return (True, getattr(meta, "rev", None), getattr(meta, "content_hash", None))

    def list(self, folder: str) -> list[dict]:
        def go(c):
            out = []
            res = c.files_list_folder(folder, recursive=True)
            while True:
                for e in res.entries:
                    if hasattr(e, "rev"):
                        out.append({"path": e.path_display, "rev": e.rev, "content_hash": e.content_hash, "size": e.size})
                if not res.has_more: break
                res = c.files_list_folder_continue(res.cursor)
            return out
        try: return self._call(go)
        except FileNotFoundError: return []
