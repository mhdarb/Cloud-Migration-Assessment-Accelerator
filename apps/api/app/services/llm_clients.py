"""Chat clients for extraction and prose (Azure OpenAI, or any OpenAI-compatible provider)."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from pydantic import BaseModel

from app.azure_clients import get_azure_openai_client
from app.config import get_settings
from app.observability import trace_span
from app.services.ports import ChatCompleter

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = ()
try:
    import openai

    _RETRYABLE_EXCEPTIONS = (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
    )
except ImportError:
    pass


class DisabledChatCompleter:
    enabled = False
    source = "none"

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        json_mode: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> str | None:
        return None


class OpenAICompatibleCompleter:
    def __init__(self, client: Any, model: str, source: str) -> None:
        self._client = client
        self._model = model
        self.source = source
        self.enabled = True

    def _call(self, **kwargs: Any) -> Any:
        settings = get_settings()
        attempts = max(1, settings.llm_max_retries + 1)
        for attempt in range(attempts):
            try:
                return self._client.chat.completions.create(
                    timeout=settings.llm_timeout_seconds, **kwargs
                )
            except _RETRYABLE_EXCEPTIONS:
                if attempt >= attempts - 1:
                    raise
                delay = settings.llm_retry_base_delay * (2**attempt) + random.uniform(0, 0.25)
                logger.warning(
                    "Chat completion transient error (%s), retrying in %.2fs (attempt %d/%d)",
                    self.source,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        json_mode: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> str | None:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "messages": messages,
        }
        if json_mode and response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": response_schema.model_json_schema(),
                    "strict": False,
                },
            }
        elif json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            with trace_span("llm.complete", source=self.source, model=self._model):
                response = self._call(**kwargs)
        except Exception:
            if json_mode:
                # Some OpenAI-compatible backends reject json_schema or json_object entirely;
                # fall back to a plain completion rather than treating this as a hard failure.
                kwargs.pop("response_format", None)
                try:
                    response = self._call(**kwargs)
                except Exception:
                    logger.exception("Chat completion failed (%s)", self.source)
                    return None
            else:
                logger.exception("Chat completion failed (%s)", self.source)
                return None
        return (response.choices[0].message.content or "").strip() or None


def get_chat_client() -> tuple[Any, str, str] | None:
    """Return (client, model, source) or None when no chat LLM is configured."""
    settings = get_settings()
    if settings.use_mock_llm:
        return None
    if settings.llm_provider == "openai_compatible" and settings.openai_base_url:
        from openai import OpenAI

        client = OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key or "not-needed")
        return client, settings.openai_model, "openai-compatible"
    if settings.azure_openai_configured:
        return get_azure_openai_client(), settings.azure_openai_deployment, "azure-openai"
    return None


def get_chat_completer() -> ChatCompleter:
    wired = get_chat_client()
    if wired is None:
        return DisabledChatCompleter()
    client, model, source = wired
    return OpenAICompatibleCompleter(client, model, source)


def clear_llm_client_cache() -> None:
    from app.azure_clients import get_azure_openai_client, get_search_client

    get_azure_openai_client.cache_clear()
    get_search_client.cache_clear()
