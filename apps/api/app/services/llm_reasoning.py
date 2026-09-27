"""Optional LLM prose layered on rule-based facts. Never changes SKUs or citations."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any

from app.services.llm_clients import get_chat_completer
from app.services.llm_prompts import (
    CONFLICT_NOTES_SYSTEM,
    QUESTION_REWRITE_SYSTEM,
    READINESS_SUMMARY_SYSTEM,
)
from app.services.ports import ChatCompleter
from app.services.text_format import plain_text

REWRITE_BATCH_SIZE = 20
REWRITE_CACHE_MAX_ENTRIES = 5000
_REWRITE_CACHE: OrderedDict[str, str] = OrderedDict()
_REWRITE_CACHE_LOCK = threading.Lock()


def _rewrite_key(model_key: str, item: dict[str, Any]) -> str:
    """Everything the rewrite depends on: model, prompt, question, facts, quotes (not the
    answer id — the same question elsewhere reuses the prose)."""
    material = json.dumps(
        [model_key, QUESTION_REWRITE_SYSTEM, item["question"], item["facts"], item["quotes"]],
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _cache_get(key: str) -> str | None:
    with _REWRITE_CACHE_LOCK:
        text = _REWRITE_CACHE.get(key)
        if text is not None:
            _REWRITE_CACHE.move_to_end(key)
        return text


def _cache_put(key: str, text: str) -> None:
    with _REWRITE_CACHE_LOCK:
        _REWRITE_CACHE[key] = text
        _REWRITE_CACHE.move_to_end(key)
        while len(_REWRITE_CACHE) > REWRITE_CACHE_MAX_ENTRIES:
            _REWRITE_CACHE.popitem(last=False)


def clear_rewrite_cache() -> None:
    with _REWRITE_CACHE_LOCK:
        _REWRITE_CACHE.clear()


class GroundedProse:
    def __init__(self, completer: ChatCompleter) -> None:
        self._completer = completer

    def rewrite_question_answer(
        self,
        question: str,
        facts: list[dict[str, Any]],
        quotes: list[str],
        fallback: str,
    ) -> tuple[str, str]:
        if not self._completer.enabled or not facts:
            return fallback, "template"
        user = json.dumps(
            {"question": question, "facts": facts, "quotes": quotes[:8]},
            ensure_ascii=True,
        )
        text = plain_text(self._completer.complete(QUESTION_REWRITE_SYSTEM, user, temperature=0.1))
        if not text:
            return fallback, "template"
        return text, "llm"

    def rewrite_question_answers(self, answers: list[dict[str, Any]]) -> None:
        if not self._completer.enabled:
            return
        payload = []
        for answer in answers:
            facts = answer.get("facts") or []
            quotes = [
                item.get("quote") or ""
                for item in answer.get("evidence") or []
                if item.get("quote")
            ]
            if not facts and not quotes:
                continue
            payload.append(
                {
                    "id": answer["id"],
                    "question": answer["question"],
                    "facts": facts,
                    "quotes": quotes[:8],
                }
            )
        # Answers whose question, facts and quotes are unchanged since they were last
        # rewritten reuse that prose: a review click changes one or two answers, and
        # re-sending all of them to the LLM made every click slow.
        by_id: dict[str, str] = {}
        todo: list[dict[str, Any]] = []
        keys: dict[str, str] = {}
        model_key = getattr(self._completer, "cache_key", None)
        for item in payload:
            if model_key is None:
                todo.append(item)
                continue
            key = _rewrite_key(model_key, item)
            keys[item["id"]] = key
            cached = _cache_get(key)
            if cached is None:
                todo.append(item)
            else:
                by_id[item["id"]] = cached
        # A client questionnaire can carry hundreds of questions; one call for all of them
        # would overrun the model's output budget and lose every rewrite on truncation.
        for start in range(0, len(todo), REWRITE_BATCH_SIZE):
            fresh = self._rewrite_batch(todo[start : start + REWRITE_BATCH_SIZE])
            by_id.update(fresh)
            for answer_id, prose_text in fresh.items():
                if answer_id in keys:
                    _cache_put(keys[answer_id], prose_text)
        for answer in answers:
            text = by_id.get(answer["id"])
            if text:
                answer["answer"] = text
                answer["answer_source"] = "llm"

    def _rewrite_batch(self, payload: list[dict[str, Any]]) -> dict[str, str]:
        raw = self._completer.complete(
            QUESTION_REWRITE_SYSTEM,
            json.dumps(payload, ensure_ascii=True),
            temperature=0.1,
            json_mode=True,
        )
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            rows = parsed.get("answers") if isinstance(parsed, dict) else parsed
            return {
                item["id"]: plain_text(item["text"])
                for item in rows or []
                if isinstance(item, dict) and item.get("id") and item.get("text")
            }
        except Exception:
            return {}

    def explain_conflict(self, payload: dict[str, Any], fallback: str) -> str:
        if not self._completer.enabled:
            return fallback
        text = plain_text(
            self._completer.complete(
                CONFLICT_NOTES_SYSTEM,
                json.dumps(payload, ensure_ascii=True),
                temperature=0.1,
            )
        )
        return text or fallback

    def rewrite_readiness_summary(self, payload: dict[str, Any], fallback: str) -> str:
        if not self._completer.enabled:
            return fallback
        text = plain_text(
            self._completer.complete(
                READINESS_SUMMARY_SYSTEM,
                json.dumps(payload, ensure_ascii=True),
                temperature=0.1,
            )
        )
        return text or fallback


def get_grounded_prose() -> GroundedProse:
    return GroundedProse(get_chat_completer())


def rewrite_question_answer(
    question: str,
    facts: list[dict[str, Any]],
    quotes: list[str],
    fallback: str,
    *,
    completer: ChatCompleter | None = None,
) -> tuple[str, str]:
    return GroundedProse(completer or get_chat_completer()).rewrite_question_answer(
        question, facts, quotes, fallback
    )


def explain_conflict(
    payload: dict[str, Any],
    fallback: str,
    *,
    completer: ChatCompleter | None = None,
) -> str:
    return GroundedProse(completer or get_chat_completer()).explain_conflict(payload, fallback)


def rewrite_readiness_summary(
    payload: dict[str, Any],
    fallback: str,
    *,
    completer: ChatCompleter | None = None,
) -> str:
    return GroundedProse(completer or get_chat_completer()).rewrite_readiness_summary(
        payload, fallback
    )
