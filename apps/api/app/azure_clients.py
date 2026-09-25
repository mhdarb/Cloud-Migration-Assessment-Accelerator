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


@lru_cache
def get_search_index_client():
    """Management client for creating/inspecting indexes (control-plane operations)."""
    from azure.search.documents.indexes import SearchIndexClient

    settings = get_settings()
    if not settings.azure_search_endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    return SearchIndexClient(
        endpoint=settings.azure_search_endpoint,
        credential=get_search_credential(),
    )


# Indexes we've already verified/created this process, so we don't re-check every upsert.
_ensured_indexes: set[str] = set()


def ensure_search_index(dimensions: int) -> None:
    """Create the configured Azure AI Search index (hybrid text + vector) if it doesn't
    already exist. Idempotent and cached per process. `dimensions` is the embedding vector
    length, taken from the vectors being indexed so the schema matches the embedder in use.

    Creating an index is a control-plane operation: the credential needs index-management
    rights (a Search *admin* key, or the "Search Service Contributor" role for Managed
    Identity). A query-only key will get a clear authorization error from Azure here.
    """
    settings = get_settings()
    name = settings.azure_search_index
    if name in _ensured_indexes:
        return

    from azure.core.exceptions import ResourceNotFoundError

    client = get_search_index_client()
    try:
        client.get_index(name)
        _ensured_indexes.add(name)
        return
    except ResourceNotFoundError:
        logger.info("Azure AI Search index '%s' not found; creating it", name)

    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="assessment_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32),
        SimpleField(name="page", type=SearchFieldDataType.Int32),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="cmaa-hnsw-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="cmaa-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="cmaa-hnsw-profile", algorithm_configuration_name="cmaa-hnsw"
            )
        ],
    )
    client.create_index(SearchIndex(name=name, fields=fields, vector_search=vector_search))
    logger.info("Created Azure AI Search index '%s' (%d-dim vectors)", name, dimensions)
    _ensured_indexes.add(name)


def identity_mode() -> str:
    settings = get_settings()
    if settings.use_managed_identity:
        return "managed_identity"
    if settings.azure_openai_api_key or settings.azure_search_api_key:
        return "api_key"
    return "none"
