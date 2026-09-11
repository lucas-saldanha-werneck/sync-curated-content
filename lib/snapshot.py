"""Snapshot da wiki via Dropbox (com rev por arquivo) e repo git de undo fora do vault."""
from __future__ import annotations
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Snap:
    files: dict = field(default_factory=dict)     # rel → (bytes, rev)


class Snapshot:
    MAX_FILES = 300

    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg

    def pull(self) -> Snap:
        root = self.cfg.vault_root.rstrip("/")
        snap = Snap()
        entries = [e for e in self.store.list(self.cfg.vault_path(self.cfg.wiki_dir)) if e["path"].endswith(".md")]
        if len(entries) > self.MAX_FILES:
            raise RuntimeError(f"wiki com {len(entries)} arquivos .md > {self.MAX_FILES}")
        for e in entries:
            rel = e["path"][len(root) + 1:]
            snap.files[rel] = self.store.download(e["path"])
        try:
            snap.files["CLAUDE.md"] = self.store.download(self.cfg.vault_path("CLAUDE.md"))
        except FileNotFoundError:
            pass
        return snap


class UndoRepo:
    def __init__(self, path: Path, runner=subprocess.run):
        self.path = Path(path)
        self.run = runner
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            self._git("init", "-q")
            self._git("config", "user.name", "sync-raindrop")
            self._git("config", "user.email", "sync-raindrop@local")

    def _git(self, *args, check=True):
        return self.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=check)

    def commit_snapshot(self, snap: Snap, message: str) -> str:
        for rel, (data, _rev) in snap.files.items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self._git("add", "-A")
        r = self._git("commit", "-q", "-m", message, check=False)
        if r.returncode != 0:
            return ""                                   # nada a commitar
        return self._git("rev-parse", "HEAD").stdout.strip()

    def show(self, sha: str, rel: str) -> bytes | None:
        r = self.run(["git", "-C", str(self.path), "show", f"{sha}:{rel}"], capture_output=True, check=False)
        return r.stdout if r.returncode == 0 else None
