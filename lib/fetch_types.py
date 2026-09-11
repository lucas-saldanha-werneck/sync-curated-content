"""Tipos compartilhados entre os fetchers (vive fora de fetcher.py p/ evitar import circular)."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class FetchResult:
    platform: str
    text: str = ""
    method: str = "none"
    title: str | None = None
    author: str | None = None
    duration_s: float | None = None
    frames: list = field(default_factory=list)      # [(nome, bytes)]
    extra: dict = field(default_factory=dict)
    error: str | None = None
