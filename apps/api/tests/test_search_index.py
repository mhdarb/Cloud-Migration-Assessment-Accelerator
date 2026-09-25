"""Auto-provisioning of the Azure AI Search index (create-if-missing).

Uses a fake SearchIndexClient so it runs with no Azure service.
"""

from __future__ import annotations

import app.azure_clients as ac
from app.config import get_settings


class _FakeIndexClient:
    def __init__(self, exists: bool):
        self._exists = exists
        self.created: list = []
        self.get_calls = 0

    def get_index(self, name):
        self.get_calls += 1
        if self._exists:
            return object()
        from azure.core.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError("index not found")

    def create_index(self, index):
        self.created.append(index)
        return index


def test_creates_index_when_missing(monkeypatch):
    ac._ensured_indexes.clear()
    fake = _FakeIndexClient(exists=False)
    monkeypatch.setattr(ac, "get_search_index_client", lambda: fake)

    ac.ensure_search_index(1536)

    assert len(fake.created) == 1
    index = fake.created[0]
    assert index.name == get_settings().azure_search_index  # "cmaa-chunks" by default
    vector_field = next(f for f in index.fields if f.name == "content_vector")
    assert vector_field.vector_search_dimensions == 1536
    key_field = next(f for f in index.fields if f.name == "id")
    assert key_field.key is True


def test_ensure_is_idempotent(monkeypatch):
    ac._ensured_indexes.clear()
    fake = _FakeIndexClient(exists=False)
    monkeypatch.setattr(ac, "get_search_index_client", lambda: fake)

    ac.ensure_search_index(1536)
    ac.ensure_search_index(1536)  # cached — must not create or re-check

    assert len(fake.created) == 1
    assert fake.get_calls == 1


def test_skips_creation_when_index_exists(monkeypatch):
    ac._ensured_indexes.clear()
    fake = _FakeIndexClient(exists=True)
    monkeypatch.setattr(ac, "get_search_index_client", lambda: fake)

    ac.ensure_search_index(1536)

    assert fake.created == []
