from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppProfile = Literal["local", "lz"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # local = laptop demo; lz = Azure AI Landing Zone spoke (strict)
    app_profile: str = "local"

    database_url: str = "sqlite+pysqlite:///./data/cmaa.db"
    storage_dir: str = "./data/uploads"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    azure_openai_api_version: str = "2024-08-01-preview"

    azure_search_endpoint: str = ""
    azure_search_api_key: str = ""
    azure_search_index: str = "cmaa-chunks"

    # Prefer Entra Managed Identity in lz profile (no API keys in app config)
    use_managed_identity: bool = False

    # Optional shared ingress expectation (documented; not enforced at runtime)
    apim_base_url: str = ""

    # Optional simple gate for lz demos (prefer Entra JWT at APIM in production)
    api_auth_key: str = ""

    relationship_inferencer: str = "heuristic"  # "off" | "heuristic" | "llm"

    confidence_review_threshold: float = 0.7
    mock_llm: bool = True
    rag_enabled: bool = True
    rag_top_k: int = 8
    local_embeddings: bool = True
    local_embedding_model: str = "all-MiniLM-L6-v2"
    embeddings_dir: str = "./data/embeddings"
    api_cors_origins: str = "http://localhost:3000"
    enforce_review: bool = True
    sizing_region: str = "eastus"
    pricing_currency: str = "USD"
    azure_pricing_live: bool = False
    pricing_timeout_seconds: float = 3.0

    # AI / content guardrails
    guardrails_enabled: bool = True
    guardrails_block_on_injection: bool = True
    guardrails_block_on_harm: bool = True
    azure_content_safety_endpoint: str = ""
    azure_content_safety_key: str = ""
    content_safety_severity_threshold: int = 2  # 0-7 scale; block at/above

    applicationinsights_connection_string: str = ""
    otel_enabled: bool = False

    # LLM completer resilience + agent cost/loop budget (P0 hardening)
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_retry_base_delay: float = 0.5
    agent_max_llm_calls: int = 40
    agent_wall_clock_seconds: float = 240.0

    # Chunking (P1)
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 80
    chunk_inventory_rows: int = 20

    # Parent-child chunking: retrieval stays on the precise (child) chunk; the extractor
    # additionally sees a bounded window of same-document neighbors for interpretation --
    # never independently citable, so evidence-quote grounding is unaffected.
    parent_context_enabled: bool = True
    parent_context_radius: int = 1
    parent_context_max_chars: int = 600

    # Retrieval (P1)
    vector_score_threshold: float = 0.02
    retrieval_hybrid_enabled: bool = True
    retrieval_rrf_k: int = 60
    reranker: str = "off"  # "off" | "cross_encoder" | "llm"
    reranker_candidate_pool: int = 20
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Provider-agnostic chat model layer (P2)
    llm_provider: str = ""  # "" = auto (Azure), or "openai_compatible"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Guardrails / hardening (P3)
    pii_redaction_mode: str = "regex"  # "regex" | "off"
    content_safety_fail_open: bool = True
    guardrails_semantic_check_enabled: bool = False

    # Pipeline locking (P3)
    pipeline_lock_backend: str = "memory"  # "memory" | "db"
    pipeline_lock_stale_seconds: float = 1800.0  # 0 disables stealing

    # Dynamic architecture (extraction strategy, RAG planning, doc classification, questions)
    extraction_strategy: str = "llm"  # "llm" | "heuristic" | "ensemble"
    rag_planner: str = "heuristic"  # "heuristic" | "off"
    llm_planner_max_followups: int = 3
    doc_classifier: str = "llm"  # "llm" | "keyword"
    doc_classify_review_threshold: float = 0.6
    question_planner_enabled: bool = True
    question_planner_max_questions: int = 5

    @field_validator("app_profile")
    @classmethod
    def _normalize_profile(cls, v: str) -> str:
        value = (v or "local").strip().lower()
        if value not in {"local", "lz"}:
            raise ValueError("APP_PROFILE must be 'local' or 'lz'")
        return value

    @property
    def is_lz_profile(self) -> bool:
        return self.app_profile == "lz"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def azure_openai_configured(self) -> bool:
        if not self.azure_openai_endpoint:
            return False
        if self.use_managed_identity:
            return True
        return bool(self.azure_openai_api_key)

    @property
    def azure_search_configured(self) -> bool:
        if not self.azure_search_endpoint:
            return False
        if self.use_managed_identity:
            return True
        return bool(self.azure_search_api_key)

    @property
    def content_safety_configured(self) -> bool:
        return bool(self.azure_content_safety_endpoint and self.azure_content_safety_key)

    @property
    def openai_compatible_configured(self) -> bool:
        return self.llm_provider == "openai_compatible" and bool(self.openai_base_url)

    @property
    def chat_llm_configured(self) -> bool:
        return self.azure_openai_configured or self.openai_compatible_configured

    @property
    def use_mock_llm(self) -> bool:
        if self.is_lz_profile:
            return False
        return self.mock_llm or not self.chat_llm_configured

    @property
    def use_azure_embeddings(self) -> bool:
        if self.is_lz_profile:
            return self.azure_openai_configured
        return self.azure_openai_configured and not self.local_embeddings

    @property
    def embeddings_mode(self) -> str:
        if self.use_azure_embeddings:
            return "azure"
        if self.local_embeddings or self.rag_enabled:
            return "local"
        return "none"

    @property
    def embeddings_path(self) -> Path:
        return Path(self.embeddings_dir)

    def ensure_dirs(self) -> None:
        Path(self.storage_dir).mkdir(parents=True, exist_ok=True)
        Path("./data").mkdir(parents=True, exist_ok=True)
        self.embeddings_path.mkdir(parents=True, exist_ok=True)

    def validate_profile(self) -> None:
        """Fail fast for Azure AI Landing Zone spoke requirements."""
        if not self.is_lz_profile:
            return
        errors: list[str] = []
        if self.mock_llm:
            errors.append("MOCK_LLM must be false in APP_PROFILE=lz")
        if self.local_embeddings:
            errors.append("LOCAL_EMBEDDINGS must be false in APP_PROFILE=lz (use shared Azure embeddings)")
        if not self.azure_openai_endpoint:
            errors.append("AZURE_OPENAI_ENDPOINT required (shared AI Foundry / OpenAI in hub or spoke)")
        if not self.use_managed_identity and not self.azure_openai_api_key:
            errors.append("USE_MANAGED_IDENTITY=true (recommended) or AZURE_OPENAI_API_KEY required")
        if not self.azure_search_endpoint:
            errors.append("AZURE_SEARCH_ENDPOINT required (shared Azure AI Search)")
        if not self.use_managed_identity and not self.azure_search_api_key:
            errors.append("USE_MANAGED_IDENTITY=true or AZURE_SEARCH_API_KEY required")
        if not self.rag_enabled:
            errors.append("RAG_ENABLED must be true in APP_PROFILE=lz")
        if not self.guardrails_enabled:
            errors.append("GUARDRAILS_ENABLED must be true in APP_PROFILE=lz")
        if self.database_url.startswith("sqlite"):
            errors.append(
                "DATABASE_URL must not be SQLite in APP_PROFILE=lz "
                "(use Postgres in the application landing zone)"
            )
        if not (self.api_auth_key or "").strip() and not (self.apim_base_url or "").strip():
            errors.append(
                "APP_PROFILE=lz requires API_AUTH_KEY or APIM_BASE_URL "
                "(prefer Entra JWT at APIM; spoke must not be open)"
            )
        if self.content_safety_fail_open:
            errors.append(
                "CONTENT_SAFETY_FAIL_OPEN must be false in APP_PROFILE=lz "
                "(a content-safety call error must block, not silently allow)"
            )
        if self.pipeline_lock_backend != "db":
            errors.append(
                "PIPELINE_LOCK_BACKEND must be 'db' in APP_PROFILE=lz "
                "(in-process locking is not safe across multiple workers)"
            )
        if errors:
            raise RuntimeError(
                "APP_PROFILE=lz validation failed (Azure AI Landing Zone spoke):\n- "
                + "\n- ".join(errors)
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # In lz, prefer MI unless explicitly disabled
    if settings.is_lz_profile and not settings.use_managed_identity:
        # allow explicit key-based lz for break-glass; no auto-flip
        pass
    settings.ensure_dirs()
    return settings
