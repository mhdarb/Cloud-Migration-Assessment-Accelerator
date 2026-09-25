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


MARKDOWN_REPLY = """### Sizing recommendation for **billing-01**

The server needs **7 vCPUs** and *27 GB* of RAM, so `Standard_D8s_v5` was chosen.

- **VM:** Standard_D8s_v5 (8 vCPU / 32 GB)
- **Disk:** P30 Premium SSD
"""


def test_markdown_explanation_is_stored_as_plain_text(monkeypatch):
    """Azure OpenAI answers in Markdown by habit; the UI and exports show it literally."""
    seen: dict = {}

    class Completer:
        enabled = True
        source = "azure-openai"

        def complete(self, system, user, *, temperature=0.1, json_mode=False):
            seen["system"], seen["user"] = system, user
            return MARKDOWN_REPLY

    monkeypatch.setattr("app.services.llm_clients.get_chat_completer", lambda: Completer())
    result = size_profile(normalize_workload(_linux_server()))

    text = result["explanation"]
    assert not any(token in text for token in ("#", "**", "`", "- **"))
    assert text.startswith("Sizing recommendation for billing-01")
    assert "The server needs 7 vCPUs and 27 GB of RAM, so Standard_D8s_v5 was chosen." in text
    assert "\n\n• VM: Standard_D8s_v5 (8 vCPU / 32 GB)\n• Disk: P30 Premium SSD" in text
    # The prompt asks for plain prose, and only the facts a short explanation needs go out.
    assert "no Markdown" in seen["system"]
    assert "rejected_candidates" not in seen["user"] and "input_profile" not in seen["user"]
    assert "Standard_D8s_v5" in seen["user"] or result["recommended_sku"] in seen["user"]


def test_template_explanation_prints_whole_numbers_cleanly(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "")
    get_settings.cache_clear()
    result = size_profile(normalize_workload(_linux_server()))
    assert ".0 IOPS" not in result["explanation"]
