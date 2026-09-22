from types import SimpleNamespace

import openai

from app.config import get_settings
from app.services.llm_clients import OpenAICompatibleCompleter


def _response(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class _FlakyClient:
    """Raises a retryable error N times, then succeeds."""

    def __init__(self, fail_times: int, exc: Exception):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return _response("ok")


def _rate_limit_error() -> Exception:
    request = openai.APIConnectionError(request=SimpleNamespace())
    return request


def test_completer_retries_on_transient_error_then_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY", "0.001")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.llm_clients.time.sleep", lambda *_: None)

    client = _FlakyClient(fail_times=2, exc=_rate_limit_error())
    completer = OpenAICompatibleCompleter(client, "gpt-4o", "test")
    result = completer.complete("system", "user")
    assert result == "ok"
    assert client.calls == 3


def test_completer_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY", "0.001")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.llm_clients.time.sleep", lambda *_: None)

    client = _FlakyClient(fail_times=99, exc=_rate_limit_error())
    completer = OpenAICompatibleCompleter(client, "gpt-4o", "test")
    result = completer.complete("system", "user")
    assert result is None
    assert client.calls == 2  # initial attempt + 1 retry


def test_completer_passes_timeout_to_client(monkeypatch):
    get_settings.cache_clear()
    seen_kwargs = {}

    class _Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            seen_kwargs.update(kwargs)
            return _response("ok")

    completer = OpenAICompatibleCompleter(_Client(), "gpt-4o", "test")
    completer.complete("system", "user")
    assert seen_kwargs["timeout"] == get_settings().llm_timeout_seconds


def test_completer_falls_back_from_json_schema_to_json_object(monkeypatch):
    from app.schemas.api import ExtractionResult

    get_settings.cache_clear()
    attempts = []

    class _Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            attempts.append(kwargs.get("response_format"))
            if kwargs.get("response_format", {}).get("type") == "json_schema":
                raise ValueError("json_schema not supported")
            return _response("{}")

    completer = OpenAICompatibleCompleter(_Client(), "gpt-4o", "test")
    result = completer.complete(
        "system", "user", json_mode=True, response_schema=ExtractionResult
    )
    assert result == "{}"
    assert len(attempts) == 2
    assert attempts[0]["type"] == "json_schema"
    assert attempts[1] is None  # retry drops response_format entirely, per the fallback path
