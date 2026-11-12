"""Registros imutáveis por item em raw/curated/_catalog/r<id>.<kind>.json = estado autoritativo."""
from __future__ import annotations
import json, re
from datetime import datetime
from lib.dropbox_store import DropboxStore, ConflictError

TERMINAL = ("wiki", "parcial", "skip", "erro", "nowiki")
ORDER = ["meta", "retry1", "retry2", "retry3", "captured", "distill", "backfill", "wiki", "parcial", "skip", "erro", "nowiki"]
NAME_RE = re.compile(r"/r(\d+)\.([a-z0-9]+)\.json$")
# Gerações: registros são imutáveis, então "re-destilar" (undo + retry) abre uma geração nova: `redoN` reabre o item e,
# a partir daí, os kinds de destilação/decisão da geração N chamam-se distillN / wikiN / parcialN / skipN / erroN.
# `nowiki` (opt-out do usuário) não tem geração. Geração 0 = nomes sem sufixo (compatível com o histórico).
GEN_KINDS = ("captured", "distill", "wiki", "parcial", "skip", "erro")
GEN_RE = re.compile(r"^(captured|distill|wiki|parcial|skip|erro|redo|recap|undone)(\d+)$")
# `undoneN` = o lote da geração N foi desfeito (undo): o item segue terminal (não volta para a fila sozinho),
# mas sai do catálogo e do índice do hook até um `retry` abrir a geração N+1.

def generation(kinds: set[str]) -> int:
    """Gerações são abertas por `redoN` (só re-destilar) ou `recapN` (refazer captura E destilação)."""
    return sum(1 for k in kinds
               if (k.startswith("redo") and k[4:].isdigit()) or (k.startswith("recap") and k[5:].isdigit()))

def gen_kind(kind: str, kinds: set[str]) -> str:
    """Nome do kind `kind` na geração atual do item (ex.: "distill" → "distill2" após 2 redos)."""
    g = generation(kinds)
    return kind if g == 0 or kind not in GEN_KINDS else f"{kind}{g}"

def has_distill(kinds: set[str]) -> bool:
    return gen_kind("distill", kinds) in kinds


def is_undone(kinds: set[str]) -> bool:
    return f"undone{generation(kinds)}" in kinds


def needs_recapture(kinds: set[str]) -> bool:
    """A geração atual foi aberta por `recapN` e ainda não tem captura própria."""
    g = generation(kinds)
    return g > 0 and f"recap{g}" in kinds and gen_kind("captured", kinds) not in kinds


def capture_kind(kinds: set[str]) -> str | None:
    """Nome do registro de captura que vale para a geração atual: o da própria geração quando
    existe (recaptura), senão o mais recente anterior — um `redo` reaproveita o raw que já havia."""
    for n in range(generation(kinds), -1, -1):
        k = "captured" if n == 0 else f"captured{n}"
        if k in kinds:
            return k
    return None


def has_capture(kinds: set[str]) -> bool:
    """Existe raw utilizável para destilar. Numa geração de recaptura só vale a captura DELA;
    numa geração de redo o raw da geração anterior serve (o problema era a destilação)."""
    if needs_recapture(kinds):
        return False
    return capture_kind(kinds) is not None

def terminal_state(kinds: set[str]) -> str | None:
    if gen_kind("wiki", kinds) in kinds:    # wiki é sucesso: prioridade máxima
        return "wiki"
    best = None
    for k in TERMINAL:                      # senão pick último (estado de falha mais avançado), na geração atual
        if gen_kind(k, kinds) in kinds:
            best = k
    return best

def record_path(cfg, item_id: int, kind: str) -> str:
    return cfg.vault_path(f"{cfg.catalog_dir}/r{item_id}.{kind}.json")

class Records:
    def __init__(self, store: DropboxStore, cfg):
        self.store, self.cfg = store, cfg
        self._map: dict[int, set[str]] = {}

    def write(self, item_id: int, kind: str, data: dict) -> str:
        assert kind in ORDER or GEN_RE.match(kind), kind
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

    def latest_distill(self, item_id: int) -> dict | None:
        """Registro distill da geração atual (None se o item foi reaberto por redo e ainda não re-destilou)."""
        kinds = self.kinds(item_id)
        if not has_distill(kinds) or is_undone(kinds):
            return None                     # desfeito: não volta ao catálogo no lote seguinte
        return self.read(item_id, gen_kind("distill", kinds))

    def mark_undone(self, item_id: int, run_id: str) -> str | None:
        """Undo de um lote: grava `undoneN` (geração atual) para o catálogo não regerar a linha do item."""
        kinds = self.kinds(item_id)
        if not has_distill(kinds) or is_undone(kinds):
            return None
        kind = f"undone{generation(kinds)}"
        self.write(item_id, kind, {"run_id": run_id})
        return kind

    def catalog_entries(self) -> list[dict]:
        out = []
        for item_id in sorted(self._map):
            d = self.latest_distill(item_id)
            if d: out.append(d)
        return out

    def request_redo(self, item_id: int) -> str | None:
        """`retry` de item já destilado: grava redoN (imutável) e reabre o item para nova destilação.
        Devolve o kind gravado, ou None se o item ainda não tem distill na geração atual (retry comum basta)."""
        kinds = self.kinds(item_id)
        if not has_distill(kinds) and terminal_state(kinds) in (None, "nowiki"):
            return None                     # ainda na fila (retry comum basta) ou opt-out do usuário (irreversível por aqui)
        kind = f"redo{generation(kinds) + 1}"
        self.write(item_id, kind, {"prev": gen_kind("distill", kinds), "reason": "retry após destilação (undo/retry)"})
        return kind

    def request_recapture(self, item_id: int) -> str | None:
        """`recapture`: reabre o item e manda refazer a CAPTURA (raw novo), não só a destilação.
        Para raw que nasceu vazio por bloqueio na origem — re-destilar o mesmo arquivo não adianta.
        Devolve o kind gravado, ou None se o item já está reaberto/na fila."""
        kinds = self.kinds(item_id)
        if not has_distill(kinds) and terminal_state(kinds) in (None, "nowiki"):
            return None
        kind = f"recap{generation(kinds) + 1}"
        self.write(item_id, kind, {"prev": gen_kind("captured", kinds), "reason": "recaptura: raw sem conteúdo utilizável"})
        return kind
