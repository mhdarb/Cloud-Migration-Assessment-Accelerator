"""Optional LLM prose layered on rule-based facts. Never changes SKUs or citations."""

from __future__ import annotations

import json
from typing import Any

from app.services.llm_clients import get_chat_completer
from app.services.llm_prompts import (
    CONFLICT_NOTES_SYSTEM,
    QUESTION_REWRITE_SYSTEM,
    READINESS_SUMMARY_SYSTEM,
)
from app.services.ports import ChatCompleter

REWRITE_BATCH_SIZE = 20


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
        text = self._completer.complete(QUESTION_REWRITE_SYSTEM, user, temperature=0.1)
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
        # A client questionnaire can carry hundreds of questions; one call for all of them
        # would overrun the model's output budget and lose every rewrite on truncation.
        by_id: dict[str, str] = {}
        for start in range(0, len(payload), REWRITE_BATCH_SIZE):
            by_id.update(self._rewrite_batch(payload[start : start + REWRITE_BATCH_SIZE]))
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
                item["id"]: item["text"]
                for item in rows or []
                if isinstance(item, dict) and item.get("id") and item.get("text")
            }
        except Exception:
            return {}

    def explain_conflict(self, payload: dict[str, Any], fallback: str) -> str:
        if not self._completer.enabled:
            return fallback
        text = self._completer.complete(
            CONFLICT_NOTES_SYSTEM,
            json.dumps(payload, ensure_ascii=True),
            temperature=0.1,
        )
        return text or fallback

    def rewrite_readiness_summary(self, payload: dict[str, Any], fallback: str) -> str:
        if not self._completer.enabled:
            return fallback
        text = self._completer.complete(
            READINESS_SUMMARY_SYSTEM,
            json.dumps(payload, ensure_ascii=True),
            temperature=0.1,
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
