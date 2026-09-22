from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Signal:
    kind: str  # e.g. "regex_injection", "semantic_injection"
    score: float
    detail: str = ""
