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
    # A skill flagged `exhaustive_doc_types` (e.g. servers/sizing over inventory) pulls
    # *every* chunk of those doc types, not just the top-k, so no server is silently left
    # unsized. This caps how many such chunks feed one extraction pass to bound the LLM
    # context; beyond it, a coverage gap is surfaced rather than silently dropping rows.
    agent_exhaustive_chunk_cap: int = 60

    # Chunking (P1)
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 80
    chunk_inventory_rows: int = 20

    # Parsing robustness (P0/P1/P2): guardrails so "any document" degrades to a visible
    # gap instead of silent data loss, an OOM, or a runaway chunk count.
    #
    # A parsed doc yielding fewer than this many characters per page is treated as
    # empty/scanned and surfaced as a report gap rather than accepted as zero content.
    min_chars_per_page: int = 8
    # OCR fallback for image-only/scanned PDF pages. Off by default: it needs the optional
    # `pytesseract` package AND a system `tesseract` binary. When either is missing the
    # code degrades to the empty-extraction gap above -- it never hard-fails ingest.
    ocr_enabled: bool = False
    ocr_language: str = "eng"
    # Use `pdfplumber` (when installed) to pull structured tables out of PDFs and emit the
    # same <<TABLE>> blocks docx does, so inventory-in-PDF gets row/summary chunking.
    # Falls back to plain `pypdf` text extraction when pdfplumber is unavailable.
    pdf_table_extraction: bool = True
    # After the line-ruled pass finds nothing, retry with a text-alignment strategy to
    # catch borderless (whitespace-aligned) tables. OFF by default: pdfplumber's text
    # strategy is aggressive and will carve ordinary or two-column prose into a fake grid
    # (even splitting words mid-token), and prose/two-column PDFs are far more common than
    # borderless-table PDFs. Line-ruled table detection stays on regardless. Enable this
    # only for a corpus you know is dominated by unruled tables.
    pdf_borderless_tables: bool = False
    # Reassemble multi-column PDF pages in true reading order (left column fully, then
    # right) instead of letting extract_text interleave the columns line by line.
    pdf_column_detection: bool = True
    # XLSX files at or under this size are opened fully (not read-only) so merged cells —
    # including merged *data* cells, not just headers — can be expanded correctly. Larger
    # files use the streaming read-only path (header forward-fill only) to bound memory.
    xlsx_full_load_max_mb: float = 25.0
    # Resource caps -- exceed one and the document is truncated with a surfaced gap.
    max_file_mb: float = 100.0
    max_pages_per_doc: int = 5000
    max_rows_per_sheet: int = 200000
    max_chunks_per_doc: int = 20000

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
        if not self.enforce_review:
            errors.append(
                "ENFORCE_REVIEW must be true in APP_PROFILE=lz "
                "(an assessment must not be marked migration-ready with unreviewed items)"
            )
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
