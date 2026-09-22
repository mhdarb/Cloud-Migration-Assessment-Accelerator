"""Shape of the chunk payload dicts passed between retrieval and extraction.

Documents the `{chunk_id, document_id, page, text, ...}` dict shape used across
`ports.py`, `agent_extract.py`, `heuristic_extract.py`, and `guardrails.py` — not
runtime-enforced (these are plain dicts, JSON-serialized straight into LLM prompts),
but gives readers and type checkers a single source of truth for the fields.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class ChunkPayload(TypedDict):
    chunk_id: str
    document_id: str
    page: int | None
    text: str
    section_title: NotRequired[str]
    row_range: NotRequired[list[int]]
    qa_index: NotRequired[int]
    file_path: NotRequired[str]
