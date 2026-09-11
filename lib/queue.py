"""Fila derivada: itens do Raindrop sem nowiki menos os que já têm estado terminal nos registros."""
from __future__ import annotations
from lib.records import terminal_state


def is_nowiki(item: dict) -> bool:
    return any(t.strip().lower() == "nowiki" for t in item.get("tags", []))


def needs_capture(item: dict, kinds: set[str]) -> bool:
    return "captured" not in kinds and terminal_state(kinds) is None


def needs_distill(kinds: set[str]) -> bool:
    return "captured" in kinds and "distill" not in kinds and terminal_state(kinds) is None


def retry_number(kinds: set[str]) -> int:
    return sum(1 for k in kinds if k.startswith("retry"))


def pending(items: list[dict], records_map: dict[int, set[str]], queue_max: int) -> tuple[list[dict], bool]:
    out = []
    for it in sorted(items, key=lambda x: x.get("created", ""), reverse=True):
        if is_nowiki(it):
            continue
        if terminal_state(records_map.get(it["id"], set())) is not None:
            continue
        out.append(it)
    full = len(out) > queue_max
    return out[:queue_max], full
