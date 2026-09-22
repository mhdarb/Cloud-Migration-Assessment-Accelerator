# Cloud Migration Assessment Accelerator — Agentic Engineering Review & Modernization Plan

**Reviewer role:** SDE 3 / staff-level code review
**Scope:** `apps/api` (FastAPI + LangGraph RAG + guardrails + Neo4j), with focus on the six themes you named — agentic engineering, intelligent chunking, a "mostly fit-for-all" (provider-agnostic) core, agent skills, agent framework & harness, and guardrails.
**Method:** Read the architecture/README docs and ~30 source files across the RAG, LLM, guardrails, orchestration, and reconciliation layers. `device_bash` on your machine wedged mid-session, so a few files (`parsers.py` full body, `report.py`, `assessment_questions.py`, `sizing.py`, `neo4j_graph.py`) were read partially or by size/interface only — noted inline where it matters.

---

## 0. TL;DR

This is a **well-architected codebase** — genuinely above average. It already has a ports/adapters composition root, a real LangGraph harness, layered guardrails, evidence-first citation validation, precedence-based reconciliation, and human-in-the-loop review. You are **not** starting from a naive prototype. The work here is *evolution*, not rewrite.

The biggest wins, in order:

1. **🔴 Fix the currently-broken `parsers.py`** (see §1) — the repo does not import cleanly right now.
2. **Intelligent chunking** — chunking is the weakest link and it's page/line-oriented, which caps retrieval quality everywhere downstream (§3).
3. **Agentic engineering** — the harness is a fixed 11-query sweep, not a planner; upgrade to a plan→act→reflect loop with a tool-using agent (§4).
4. **"Mostly fit-for-all"** — you're hard-wired to Azure OpenAI; abstract the model layer so any OpenAI-compatible / Bedrock / local model works (§5).
5. **Agent skills** — formalize each retrieval "query" into a declarative *skill* with its own schema, retriever config, and validators (§6).
6. **Guardrails** — strong foundation; add semantic injection detection, output-schema enforcement via structured outputs, cost/loop budgets, and PII handling (§7).

---

## 1. 🔴 Critical: the repo is in a broken state right now

`apps/api/app/services/parsers.py` is **323 bytes** and has the **newest mtime of any file in the project**. Its entire contents are:

```python
from __future__ import annotations
import csv, json
from dataclasses import dataclass, field
from pathlib import Path
from openpyxl import load_workbook
from pypdf import PdfReader
from docx import Document as DocxDocument
from app.models.entities import DocumentType

@dataclass
class ParsedPage:
    page: int
    # ← file ends here
```

But `ingest.py` does:

```python
from app.services.parsers import PRECEDENCE, chunk_pages, parse_file
```

None of `PRECEDENCE`, `chunk_pages`, or `parse_file` exist in the current file, and the sibling `hve/` directory is **empty**. This will raise `ImportError` at pipeline startup and break test collection. It looks like an interrupted refactor (perhaps you were extracting parsers into `hve/` or a `parsers/` package and the move didn't complete).

**Action:** restore `parsers.py` from git (`git show HEAD:apps/api/app/services/parsers.py`) or finish the refactor before anything else. Everything below assumes the pre-refactor `parsers.py` (page-based `chunk_pages`) is the intended baseline. This is also a process signal — see §8 on CI, because a green pipeline would have caught this instantly.

---

## 2. What's already good (keep these)

Worth stating explicitly so the refactor doesn't regress them:

- **Ports & adapters** (`ports.py` + `providers.py`): `Embedder`, `VectorIndex`, `Retriever`, `ChatCompleter`, `LlmExtractor`, `ClaimExtractor`, `GraphSink` are clean `Protocol`s with a single composition root. This is the right backbone for everything below.
- **Evidence-first design**: `citations.validate_citations` enforces that every claim's `chunk_ids` are in the retrieved set *and* that the `evidence_quote` is a grounded substring, else it clears refs and confidence-caps at 0.35. This is exactly right and rare.
- **Deterministic-where-it-matters**: SKU selection, precedence winners, and citations are rules, not generation. LLM is confined to prose. The docs even encode "the LLM must not pick a SKU / invent citations / override precedence." Excellent discipline.
- **Graceful degradation**: mock LLM + local embeddings + no-Neo4j still produces a full run.
- **Human-in-the-loop**: review queue gate (`ENFORCE_REVIEW`), conflict materialization, and a follow-up learning log.
- **Guardrail telemetry**: `GuardrailReport.to_metrics()` and per-node `node_trace` accumulation give you observability into the agent's decisions.

---

## 3. Intelligent chunking (highest-leverage improvement)

### Current state
Chunking is `chunk_pages(parsed.pages)` producing `(page, start, end, text)` tuples, and `chunks_to_payload` truncates chunk text to **1500 chars**. Retrieval is over whatever a "page" is (a PDF page, a spreadsheet sheet, etc.). Code snapshots are chunked as **one chunk per file** (`# FILE: {path}\n{text}`) regardless of file size.

### Why it's the weak link
Every downstream quality metric — retrieval recall, citation grounding, claim precision — is bounded by chunk quality. Page-boundary chunking has three problems here:

1. **Semantic bleed / dilution**: a CMDB spreadsheet page or a big Dockerfile becomes one giant chunk; the embedding is an average of many unrelated facts, so cosine similarity is muddy and top-k misses.
2. **Truncation loss**: hard 1500-char cut means the tail of long pages is never seen by the LLM extractor even when retrieved.
3. **No structure awareness**: tables (CMDB rows), prose (architecture docs), and manifests (JSON/YAML) all get the same treatment, yet they have completely different optimal chunk shapes.

### Recommended approach — structure-aware, typed chunking
Introduce a `Chunker` port and route by `doc_type`:

| doc_type | Chunking strategy |
|---|---|
| `inventory` (CMDB xlsx/csv) | **Row-group chunking**: header + N rows per chunk (e.g. 20), header repeated in every chunk so each is self-describing. Never split a row. |
| `architecture` / `requirements` / `runbook` (prose) | **Recursive/semantic splitting** with token-based sizing (~400–800 tokens) and **overlap (~15%)** so a fact spanning a boundary is captured on both sides. Split on headings → paragraphs → sentences. |
| `code_snapshot` (manifests) | **Structure-aware**: one chunk per top-level manifest section (per dependency block, per service in compose, per stanza in Dockerfile), not one-per-file. Keep file path in metadata, not baked into text. |
| `questionnaire` | **Q/A-pair chunking**: one chunk per question+answer. |

Concrete moves:
- **Switch from character counts to token counts** (`tiktoken` or the ST tokenizer). 1500 chars ≈ 375 tokens on prose but wildly variable on tables/code.
- **Add configurable overlap** (`CHUNK_OVERLAP_TOKENS`, default ~64). Right now there is zero overlap, so boundary facts are lost.
- **Attach richer chunk metadata** (`section_title`, `table_name`, `row_range`, `heading_path`) and use it in retrieval boosting and in the evidence trace shown to reviewers.
- **Contextual retrieval (Anthropic pattern)**: prepend a 1–2 sentence LLM-generated "situating" summary to each chunk before embedding (e.g. "This row is from the PROD CMDB export describing server host `web-01`…"). Measured to cut retrieval failures substantially. Do it once at ingest; cache it.
- **Late-chunking / parent-document retrieval**: embed small chunks for precision, but return the enclosing parent section to the extractor for context. Big recall+precision win for tables and manifests.
- **Deduplicate near-identical chunks** before indexing (common in ZIP snapshots with vendored/duplicated config).

### Retrieval upgrades that pair with chunking
`MergingRetriever` currently does vector-first, then keyword *only to backfill to k*. Improvements:
- **True hybrid fusion (RRF)**: run vector + keyword (BM25) in parallel and combine with Reciprocal Rank Fusion rather than "vector, then top up." Keyword is currently a naive term-count (`sum(1 for t in terms if t in text)`) — replace with BM25 (`rank_bm25`) or SQLite FTS5.
- **Add a re-ranker** (cross-encoder like `bge-reranker` locally, or Cohere/Azure semantic ranker in `lz`) over the fused top-N → top-k. This is usually the single biggest retrieval-quality lever after chunking.
- **The FAISS `score <= 0.02` floor is arbitrary** and dimension/normalization-dependent; make it a setting and validate it against a small labeled set.
- **`KeywordRetriever` caches all chunks on the instance keyed by a single `_cached_assessment_id`** — this is a correctness/memory hazard if the retriever instance is reused across assessments concurrently. Move caching to a request-scoped object or drop it in favor of FTS5.
- **FAISS index is `IndexFlatIP` rebuilt from scratch on every upsert** and stored one-file-per-assessment. Fine for demo scale; for real estates add HNSW and incremental add.

---

## 4. Agentic engineering & the harness

### Current state
`agent_extract.py` builds two compiled LangGraphs:
- **mock path**: `retrieve_all → heuristic extract → validate → finalize`.
- **LLM path**: for each of **11 hard-coded `RAG_QUERIES`**: `select_query → retrieve → extract → validate → (1 citation retry) → accumulate`, then **one** extra "gap" query if gaps remain.

This is a solid, *deterministic* RAG sweep. But it is **not really an agent** — there's no planning, no tool selection, no reflection loop, and the query set is fixed regardless of what documents were actually uploaded. `recursion_limit=250` is a smell that the graph is doing bounded iteration by brute force.

### Upgrades, in order of value

1. **Plan → Act → Reflect loop.** Add a `plan` node that looks at the *actual* corpus (doc types present, entity counts so far, open gaps) and decides which skills/queries to run and in what order — instead of always running all 11. For a pure-questionnaire upload you don't need the "servers/vCPU" sweep; for a CMDB-only upload you can skip prose-oriented queries. This cuts cost and improves precision.

2. **Coverage-driven termination instead of a fixed list.** Define the *target schema* (apps, servers, DBs, dependencies, NFRs, compliance…) and loop until coverage plateaus or a budget is hit. The `extra_gap_query` idea is the seed of this — generalize it into a real gap-closing loop with a max-iterations budget.

3. **Make it tool-using.** Give the agent tools rather than a fixed pipeline: `retrieve(query, doc_type_filter, k)`, `lookup_entity(key)`, `list_gaps()`, `validate_claim(claim)`. Let the model choose calls. LangGraph supports this via a tool node; you already have the retriever and validators as callables. This is the difference between "scripted RAG" and "agentic."

4. **Structured outputs over JSON-mode-and-hope.** `ChatLlmExtractor` asks for `json_object` and then `model_validate`s, falling back to a gap on parse failure. Move to **native structured outputs / tool-call schemas** (Pydantic → JSON schema passed to the API) so the model is constrained to your `ExtractionResult` shape. Eliminates the "invalid JSON" failure mode entirely.

5. **Parallelize independent queries.** The 11 queries are independent until accumulate; LangGraph can fan them out concurrently (map-reduce) instead of the current sequential `select_query → … → accumulate → select_query` chain. Big latency win with Azure OpenAI.

6. **Persist agent state / checkpointing.** Use LangGraph's checkpointer (SQLite/Postgres) so a long extraction can resume after a crash and so each run is replayable/auditable — this doubles as your eval harness (§8) and as durability for large estates.

7. **The mock and LLM graphs duplicate node logic.** Unify into one graph parameterized by the extractor and a "planner" strategy; the mock path becomes "planner returns the single batch-heuristic skill." Less drift, one place to add checkpointing/guardrails.

---

## 5. "Mostly fit-for-all" — provider-agnostic core

### Current state
`llm_clients.get_chat_client()` only knows Azure OpenAI (`azure_openai_configured` → `OpenAICompatibleCompleter`, else `DisabledChatCompleter`). Embeddings are Azure-or-local-MiniLM. The `lz` profile *requires* Azure across the board. The abstractions (`ChatCompleter`, `Embedder`) are already clean — the coupling is only in the wiring and config.

### Recommendations
- **Add a `LLM_PROVIDER` setting** (`azure_openai | openai | bedrock | anthropic | vertex | ollama | vllm`) and a small factory per provider that returns a `ChatCompleter`. Since your port is already OpenAI-shaped, the fastest "fit-for-all" is to route everything through an **OpenAI-compatible base URL** (`OPENAI_BASE_URL` + key) — that single path covers OpenAI, vLLM, Ollama, Together, Groq, LM Studio, and most gateways with zero new code.
- **Consider LiteLLM or the Anthropic/OpenAI SDKs behind the port** as the "one adapter, many providers" layer rather than hand-rolling each. Keep `ChatCompleter` as your seam so domain code never changes.
- **Decouple embedding provider from chat provider** — today `use_azure_embeddings` is entangled with `azure_openai_configured`. Someone may want local MiniLM embeddings + a hosted chat model, or Azure embeddings + a local Llama. Make them independent settings.
- **Model capability profile**: some models don't support `response_format`/temperature/system role identically. Add a small per-provider capability map so the completer degrades correctly (you already do a nice fallback when `response_format` is rejected — generalize it).
- **Retry/backoff/timeout are missing** on `OpenAICompatibleCompleter.complete` — one exception → `None` → a gap. Add bounded retries with jitter and a per-call timeout; surface rate-limit (429) distinctly from hard failures.
- **`lz` profile** should allow "any enterprise-approved gateway" (APIM in front of Azure *or* Bedrock in an AWS landing zone), not hard-require Azure OpenAI — this is what "mostly fit-for-all" means for regulated buyers.

---

## 6. Agent skills

Right now the 11 `RAG_QUERIES` are bare strings, and the extractor uses one generic system prompt (`EXTRACTION_SYSTEM`) for all of them. That works, but it leaves precision on the table and it's hard to extend. **Formalize each retrieval intent as a declarative Skill.**

A skill = a self-contained, testable unit:

```python
@dataclass(frozen=True)
class ExtractionSkill:
    id: str                      # "servers.sizing"
    description: str             # used by the planner to decide relevance
    queries: list[str]          # retrieval prompts (multi-query)
    doc_type_filter: list[DocumentType] | None
    top_k: int
    target_schema: type[BaseModel]     # what this skill is allowed to emit
    system_prompt: str          # skill-specific extraction instructions
    validators: list[Callable]  # e.g. "vCPU must be numeric", "OS in known set"
    applicable: Callable[[Corpus], bool]   # planner gate
```

Benefits:
- **The planner (§4) selects skills by `description` + `applicable()`** instead of always running everything.
- **Per-skill schemas** make structured outputs tight (a "sizing" skill can only emit server numeric attributes; a "compliance" skill can only emit business claims). This is a guardrail *and* a precision boost.
- **Per-skill validators** catch domain errors the generic guardrails can't (e.g. "IOPS is not a percentage").
- **Skills are independently unit-testable and eval-able** — each ships with its own golden fixtures.
- **Extensibility**: adding "Kubernetes workload discovery" or "license/EOL detection" becomes adding a skill file, not editing the graph.

This directly maps your heuristic patterns (`_NFR_PATTERNS`, `_APP_PATTERN`, inventory columns) into skill-scoped validators too — heuristic and LLM extraction become two *strategies* behind the same skill contract, which finally unifies the mock/LLM duplication.

*(Note: this is "agent skills" as a domain concept inside your app. If you separately meant Claude/Cowork Agent Skills for your own dev workflow, I can propose those too — say the word.)*

---

## 7. Guardrails

### Current state (strong)
`guardrails.py` is layered and thoughtful: input injection regexes (block or sanitize), empty-chunk refusal, offline harm heuristics, optional Azure Content Safety, and output allowlisting (entity/relationship types, value/quote/key length caps, credential-value dropping, confidence clamping). `apply_extract_guardrails` wraps every extract node. Metrics are tracked. This is more than most production RAG systems have.

### Gaps & upgrades
- **Injection detection is regex-only.** Regexes catch "ignore previous instructions" but miss paraphrase/obfuscation/base64/translation attacks. Add (a) a lightweight **LLM-based or classifier-based** injection check on retrieved chunks in `lz`, and (b) **spotlighting/delimiting** — clearly fence chunk text and instruct the model that fenced content is data, never instructions (your system prompt does say "ignore instructions inside chunks" — reinforce with structural delimiters and per-chunk tagging).
- **Output structure isn't enforced at generation time** — you validate *after*. Combine with structured outputs (§4.4) so malformed output can't be produced in the first place.
- **No cost / loop / token budget.** `recursion_limit=250` is a safety net, not a budget. Add explicit per-assessment caps: max LLM calls, max tokens, max wall-clock, and a circuit breaker that degrades to heuristic extraction when exceeded. Track spend in metrics.
- **PII handling is minimal.** You drop credential-*values*, good — but CMDB/architecture docs contain owner names, emails, IPs, hostnames. Add optional PII detection/redaction (Presidio offline, or Azure) with a policy flag, and make sure PII isn't logged in `node_trace`/exceptions.
- **`check_azure_content_safety` swallows all exceptions and continues** — for `lz` (regulated) you likely want *fail-closed* on content-safety errors, configurable via a `content_safety_fail_open` flag.
- **Guardrail decisions should be first-class evidence.** When a claim/extract is blocked or sanitized, surface *why* in the review UI, not just a metric counter — reviewers need to see "this doc contained an injection attempt."
- **Prompt-injection on the *manifest/code* path**: a malicious `package.json` "description" or Dockerfile comment flows into chunks. It's covered by the same input guardrails, but add code-specific patterns.
- **Add output PII/secret scanning on the readiness summary and question rewrites**, not just extraction — the prose LLM (`GroundedProse`) can echo sensitive strings from facts/quotes.

---

## 8. Framework, harness & engineering hygiene (cross-cutting)

- **🔴 CI is the missing safety net.** The `parsers.py` breakage (§1) proves it. Add a GitHub Actions (or Azure DevOps) pipeline: `ruff` + `mypy` (you have `pyproject.toml`), `pytest` on every PR, and an **import smoke test** (`python -c "import app.main"`) that would have caught this in seconds.
- **Build a RAG/agent eval harness.** You have great `node_trace`/metrics plumbing and smoke scripts (`smoke_*.py`) — turn them into a real eval: a labeled Contoso golden set + metrics for retrieval recall@k, citation-grounding rate, claim precision/recall, and cost per run. Gate PRs on no-regression. This is what lets you *safely* make the chunking/agent changes above. LangGraph checkpointing (§4.6) gives you replay for free.
- **Concurrency model.** Pipeline runs on a background thread with an in-process lock (`pipeline_lock.py`). Fine for a single-process demo; for the `lz`/multi-worker deployment, the lock won't hold across workers — move to a DB advisory lock or a proper task queue (Celery/RQ/Azure Container Apps jobs). Also the FastAPI background thread shares the process event loop; a real queue isolates long extractions.
- **Observability.** `observability.py` is small (App Insights connection string). Add **OpenTelemetry tracing** around each agent node and LLM call (LangGraph + OTel integrate well), so you can see per-skill latency/cost/failure in production, not just end-of-run counters. Emit token usage from the completer.
- **Config sprawl.** `Settings` is large and boolean-heavy (`use_mock_llm` derived from 3 flags, etc.). It works, but document the truth table (the README RAG matrix is good) and add a `GET /health/config` that echoes the *effective* resolved modes for debugging.
- **Type the `chunk_payload` dicts.** They're passed as `list[dict[str, Any]]` everywhere; a `TypedDict` (`ChunkPayload`) would catch shape bugs and self-document.
- **`hve/` empty dir and the parsers stub** suggest an in-flight refactor — finish or revert it and add a pre-commit hook so half-done moves don't land.

---

## 9. Prioritized roadmap

**P0 — Unblock & protect (days)**
1. Restore `parsers.py`; get `import app.main` + `pytest` green. (§1)
2. Add CI: ruff, mypy, pytest, import-smoke. (§8)
3. Add retry/backoff/timeout + token-budget circuit breaker to the completer. (§5, §7)

**P1 — Quality core (1–3 weeks)**
4. Token-based, structure-aware, overlapping chunking with a `Chunker` port + per-doc-type strategies. (§3)
5. Hybrid retrieval with RRF + BM25/FTS5 + a re-ranker. (§3)
6. Structured outputs for extraction; kill the JSON-parse failure mode. (§4.4)
7. Stand up the eval harness on the Contoso golden set; baseline metrics. (§8)

**P2 — Agentic & fit-for-all (3–6 weeks)**
8. Provider-agnostic model layer (`LLM_PROVIDER` / OpenAI-compatible base URL; decouple embeddings). (§5)
9. Skill abstraction; migrate the 11 queries + heuristics into skills. (§6)
10. Planner node + coverage-driven termination + parallel skill execution + checkpointing. (§4)
11. Contextual-retrieval chunk summaries + parent-document retrieval. (§3)

**P3 — Enterprise hardening (ongoing)**
12. Semantic injection detection + spotlighting; PII redaction; fail-closed content safety. (§7)
13. Distributed locking / task queue; OTel tracing + cost dashboards. (§8)
14. FAISS→HNSW/incremental (or standardize on Azure AI Search / pgvector) for large estates. (§3)

---

## 10. Files I couldn't fully verify

Because your machine's shell wedged mid-review, these were read only partially or by interface/size and should be confirmed against the P1/P2 work: `parsers.py` (full body — currently broken), `report.py`, `assessment_questions.py`, `sizing.py`, `pricing.py`, `neo4j_graph.py`, `graph.py`, `code_manifests.py` + `manifest_parsers/*`, and the entire `apps/web` frontend. Nothing in them changes the recommendations above, but the chunking/skill work touches `report.py` and `assessment_questions.py` (they consume claims/evidence), so re-read those before implementing §3/§6.
