from app.config import get_settings
from app.models.entities import Server
from app.services.llm_clients import get_chat_client
from app.services.sizing import normalize_workload, size_profile


def _linux_server() -> Server:
    return Server(
        assessment_id="a",
        name="billing-01",
        normalized_key="billing-01",
        confidence=0.9,
        attributes={
            "vcpus": "8",
            "memory_gb": "32 GB",
            "cpu_utilization_pct": "50%",
            "memory_utilization_pct": "60%",
            "disk_gb": "900",
            "disk_iops": "3200",
            "disk_throughput_mbps": "170",
            "environment": "Prod",
            "os": "RHEL 8",
        },
    )


def test_explanation_uses_template_without_llm(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "")
    get_settings.cache_clear()
    result = size_profile(normalize_workload(_linux_server()))
    assert result["explanation_source"] == "deterministic-template"
    assert "lowest-cost compatible catalog" in result["explanation"]


def test_sizing_client_uses_azure_when_configured(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    get_settings.cache_clear()
    fake = object()
    monkeypatch.setattr("app.services.llm_clients.get_azure_openai_client", lambda: fake)
    client, model, source = get_chat_client()
    assert source == "azure-openai"
    assert client is fake
    assert model == get_settings().azure_openai_deployment


def test_explanation_uses_azure_when_enabled(monkeypatch):
    class Completer:
        enabled = True
        source = "azure-openai"

        def complete(self, system, user, *, temperature=0.1, json_mode=False):
            return "Azure sized this SKU."

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.llm_clients.get_chat_completer",
        lambda: Completer(),
    )
    result = size_profile(normalize_workload(_linux_server()))
    assert result["explanation_source"] == "azure-openai"
    assert result["explanation"] == "Azure sized this SKU."
    assert result["recommended_sku"].startswith("Standard_")
