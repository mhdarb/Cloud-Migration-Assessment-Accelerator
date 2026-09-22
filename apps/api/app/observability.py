"""Lightweight observability hooks for Azure Monitor / App Insights.

When APPLICATIONINSIGHTS_CONNECTION_STRING is unset, logging stays stdlib-only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings

logger = logging.getLogger(__name__)


def configure_observability() -> None:
    settings = get_settings()
    conn = (settings.applicationinsights_connection_string or "").strip()
    if not conn:
        logger.info(
            "Observability: no APPLICATIONINSIGHTS_CONNECTION_STRING; "
            "using stdlib logging (expected for local profile)"
        )
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)
        logger.info("Observability: Azure Monitor OpenTelemetry configured")
    except Exception:
        logger.exception(
            "Observability: failed to configure Azure Monitor; continuing with stdlib logging"
        )


@contextmanager
def trace_span(name: str, **attributes: object) -> Iterator[None]:
    """No-op unless `settings.otel_enabled` and `opentelemetry-api` is installed.

    Wraps agent-node execution and chat-completion calls in spans that export via
    whatever `configure_observability()` set up (Azure Monitor today; any OTel-compatible
    backend if `configure_azure_monitor` is swapped out later). Safe to call unconditionally.
    """
    if not get_settings().otel_enabled:
        yield
        return
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
            yield
    except ImportError:
        yield
