"""Gramática restrita: o que vem do modelo só entra no vault se passar aqui."""
from __future__ import annotations
import re

class GrammarError(ValueError): ...

CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f​-‏  ﻿]")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
MDLINK = re.compile(r"\]\(([^)\s]+)\)")
URL = re.compile(r"https?://[^\s)\]]+")

def validate_text(s: str, max_len: int, allowed_pages: set[str], source_url: str | None = None) -> str:
    if s is None: raise GrammarError("texto nulo")
    s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(s) > max_len: raise GrammarError(f"texto com {len(s)} > {max_len}")
    if CTRL.search(s): raise GrammarError("caractere de controle/invisível")
    for bad in ("<", ">", "![[", "![", "```", "curated:"):
        if bad in s: raise GrammarError(f"proibido: {bad!r}")
    if s.startswith("---"): raise GrammarError("frontmatter no texto")
    for m in MDLINK.finditer(s):
        if not m.group(1).startswith("https://"): raise GrammarError(f"link com esquema proibido: {m.group(1)[:40]}")
    for u in URL.findall(s):
        if source_url is None or u.rstrip(".,") != source_url: raise GrammarError(f"url fora da fonte: {u[:60]}")
    allowed = {p.lower() for p in allowed_pages}
    for m in WIKILINK.finditer(s):
        if m.group(1).strip().lower() not in allowed: raise GrammarError(f"wikilink fora da allowlist: {m.group(1)}")
    return s

def validate_triggers(triggers: list[str], vocab: set[str]) -> list[str]:
    out = []
    for t in triggers or []:
        k = (t or "").strip().lower()
        if k in vocab and k not in out: out.append(k)
    if not out: raise GrammarError("nenhum gatilho do vocabulário")
    return out[:8]

def validate_type(t: str, types: list[str]) -> str:
    if t not in types: raise GrammarError(f"tipo fora do vocabulário: {t}")
    return t
