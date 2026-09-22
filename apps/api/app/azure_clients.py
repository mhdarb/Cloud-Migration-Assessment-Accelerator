"""Azure client helpers for AI Landing Zone (spoke) workloads.

local profile: API keys from env (demo).
lz profile: DefaultAzureCredential / Managed Identity preferred; keys only as fallback.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_azure_credential():
    """Entra credential for Managed Identity / az login / VS Code auth."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
    )


def openai_token_provider() -> str:
    credential = get_azure_credential()
    # Cognitive Services scope used by Azure OpenAI / AI Foundry
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return token.token


@lru_cache
def get_azure_openai_client():
    """Azure OpenAI client using MI (lz) or API key (local)."""
    from openai import AzureOpenAI

    settings = get_settings()
    if not settings.azure_openai_endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is required")

    if settings.use_managed_identity:
        return AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_ad_token_provider=openai_token_provider,
            api_version=settings.azure_openai_api_version,
        )
    if not settings.azure_openai_api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY required when not using managed identity")
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
    )


def get_search_credential() -> Any:
    settings = get_settings()
    if settings.use_managed_identity:
        return get_azure_credential()
    from azure.core.credentials import AzureKeyCredential

    if not settings.azure_search_api_key:
        raise RuntimeError("AZURE_SEARCH_API_KEY required when not using managed identity")
    return AzureKeyCredential(settings.azure_search_api_key)


@lru_cache
def get_search_client():
    from azure.search.documents import SearchClient

    settings = get_settings()
    if not settings.azure_search_endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index,
        credential=get_search_credential(),
    )


def identity_mode() -> str:
    settings = get_settings()
    if settings.use_managed_identity:
        return "managed_identity"
    if settings.azure_openai_api_key or settings.azure_search_api_key:
        return "api_key"
    return "none"
