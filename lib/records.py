"""Registros imutáveis por item em raw/curated/_catalog/r<id>.<kind>.json = estado autoritativo."""
from __future__ import annotations
import json, re
from datetime import datetime
from lib.dropbox_store import DropboxStore, ConflictError

TERMINAL = ("wiki", "parcial", "skip", "erro", "nowiki")
ORDER = ["meta", "retry1", "retry2", "retry3", "captured", "distill", "backfill", "wiki", "parcial", "skip", "erro", "nowiki"]
NAME_RE = re.compile(r"/r(\d+)\.([a-z0-9]+)\.json$")

def terminal_state(kinds: set[str]) -> str | None:
    if "wiki" in kinds:                     # wiki é sucesso: prioridade máxima
        return "wiki"
    best = None
    for k in TERMINAL:                      # senão pick último (estado de falha mais avançado)
        if k in kinds:
            best = k
    return best

def record_path(cfg, item_id: int, kind: str) -> str:
    return cfg.vault_path(f"{cfg.catalog_dir}/r{item_id}.{kind}.json")

class Records:
    def __init__(self, store: DropboxStore, cfg):
        self.store, self.cfg = store, cfg
        self._map: dict[int, set[str]] = {}

    def write(self, item_id: int, kind: str, data: dict) -> str:
        assert kind in ORDER, kind
        body = {"id": item_id, "kind": kind, "ts": datetime.now().astimezone().isoformat(timespec="seconds"), **data}
        payload = json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True).encode()
        try:
            rev = self.store.add(record_path(self.cfg, item_id, kind), payload)
        except ConflictError:
            # já existe com conteúdo diferente (ts diferente é normal): registro é write-once, mantém o antigo
            rev = self.store.exists(record_path(self.cfg, item_id, kind))[1] or ""
        self._map.setdefault(item_id, set()).add(kind)
        return rev

    def refresh(self) -> dict[int, set[str]]:
        m: dict[int, set[str]] = {}
        for e in self.store.list(self.cfg.vault_path(self.cfg.catalog_dir)):
            mm = NAME_RE.search(e["path"])
            if mm: m.setdefault(int(mm.group(1)), set()).add(mm.group(2))
        self._map = m
        return m

    def kinds(self, item_id: int) -> set[str]:
        return set(self._map.get(item_id, set()))

    def state(self, item_id: int) -> str | None:
        return terminal_state(self.kinds(item_id))

    def read(self, item_id: int, kind: str) -> dict | None:
        try:
            data, _ = self.store.download(record_path(self.cfg, item_id, kind))
        except FileNotFoundError:
            return None
        return json.loads(data.decode("utf-8"))

    def catalog_entries(self) -> list[dict]:
        out = []
        for item_id, kinds in sorted(self._map.items()):
            if "distill" in kinds:
                d = self.read(item_id, "distill")
                if d: out.append(d)
        return out
