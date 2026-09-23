# CMAA — Dynamic Architecture (as built)

**Status:** Implemented (Phases 0–4 below). This document originally shipped as a pre-implementation design proposal; it now describes what was actually built, including the scope calls made along the way. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system picture — this doc is the deep dive on the dynamic (model-backed) layer specifically.

## 1. The core idea

The pipeline mixes two kinds of decisions:

- **Cognitive decisions** — what a document *means*: entity/dependency extraction, which retrieval queries are worth running, what type a document is, what supplementary questions matter. These are model-backed, each with a deterministic offline fallback.
- **Governance decisions** — SKU selection, cost math, document precedence, evidence grounding. These are *deliberately* deterministic so a migration architect can defend every number to an auditor. **These were not touched.**

> **Models decide *what the documents say*; rules decide *what we do about it*.**

Every dynamic output still produces **grounded, cited, schema-valid claims**, which flow through the same unchanged deterministic gate (citations → sanitize → reconcile precedence → size). The LLM never picks a SKU, never invents a citation, never overrides precedence.

```mermaid
flowchart LR
  subgraph DYN["DYNAMIC (models + planning, all with offline fallbacks)"]
    C[DocClassifier] --> P[RagPlanner]
    P --> R[Retrieve — hybrid BM25+vector, RRF]
    R --> X[LlmExtractor — per-skill structured outputs]
    X --> Q[QuestionPlanner]
  end
  subgraph GATE["DETERMINISTIC GATE (unchanged, auditable)"]
    V[citations.validate] --> S[guardrails.sanitize]
    S --> RC[reconciliation precedence]
    RC --> SZ[sizing rules]
    SZ --> PR[pricing math]
  end
  X --> V
  Q --> V
  GATE --> OUT[(Claims / Report / Graph)]
```

The `ports.py` / `providers.py` composition root made this additive: each capability below is a new `Protocol` with an LLM implementation and a deterministic fallback, wired at the root. Nothing downstream of the gate changed.

---

## 2. Guiding principles (held throughout)

1. **Evidence-first survives.** Every dynamic output carries `chunk_ids` + `evidence_quote` and passes `citations.validate_citations` unchanged.
2. **Graceful degradation survives.** Every new dynamic port has a deterministic offline implementation (`MOCK_LLM=true` + local embeddings still produces a full run — no external graph database required at all).
3. **The deterministic gate is untouched.** `sizing.py`, `pricing.py`, `reconciliation.py`, `citations.py` were not modified.
4. **Human-in-the-loop absorbs uncertainty.** Low-confidence doc classifications surface as report gaps rather than silently mislabeling.
5. **Nothing shipped without an eval.** `tests/test_golden_extraction.py` (hand-verified against `scripts/generate_sample_data.py`'s literal planted facts, not hand-traced regexes) gates the chunking/retrieval/planner changes.
6. **Ports over rewrites.** New capability extends the existing `Protocol` seams in `ports.py` rather than restructuring the pipeline.

---

## 3. Ports added

All in `apps/api/app/services/ports.py`, each with ≥2 interchangeable implementations (Strategy pattern) wired in `providers.py`:

| Port | Implementations | Role |
|---|---|---|
| `LlmExtractor` (extended) | `ChatLlmExtractor`, `HeuristicLlmExtractor`, `EnsembleExtractor` | `extract(query, chunk_payload, *, target_schema=None, system_prompt_suffix=None) -> ExtractionResult` |
| `RagPlanner` | `HeuristicPlanner`, `NoOpRagPlanner` | `plan(profile: CorpusProfile) -> QueryPlan` — which skills' queries to run |
| `DocClassifier` | `KeywordClassifier`, `LlmClassifier` | `classify(filename, text_sample) -> Classification` |
| `QuestionPlanner` | `HeuristicQuestionPlanner`, `LlmQuestionPlanner`, `NoOpQuestionPlanner` | `plan(summary: InventorySummary) -> list[PlannedQuestion]` |
| `InjectionDetector` | `RegexInjectionDetector`, `SemanticInjectionDetector`, `CompositeInjectionDetector` | `scan(text) -> list[Signal]` |

Supporting Pydantic types live in `app/schemas/` (`extraction_targets.py`, `planning.py`, `classification.py`, `questions.py`, `guardrail_signals.py`).

**Design decision:** rather than adding a separate `ExtractionStrategy` port as originally sketched, the existing `LlmExtractor` port was extended with optional `target_schema`/`system_prompt_suffix` kwargs — it was already structurally identical, and a parallel port would have meant two seams doing the same job. `app/services/compat.call_if_supported` (`inspect.signature`-based) lets these optional kwargs be added without breaking pre-existing duck-typed callers.

---

## 4. Extraction — structured outputs, not a hardcoded name list

**Problem that existed:** `heuristic_extract.py` hardcoded literal demo entity names (`"Billing Service"`, `"Identity Gateway"`, `"Customer Portal"`) in its business-criticality detector — pure demo-data leakage. The LLM path used generic `json_object` mode with a hope-and-`model_validate` parse.

**What was built:**
- `app/schemas/extraction_targets.py` — `ExtractionTarget` base class with a `to_extraction_result()` method (Adapter pattern). Three narrow per-skill schemas — `ServerSizingTarget`, `NfrTarget`, `DependencyTarget` — each adapt their constrained shape back into the shared `ExtractedClaim`/`ExtractedDependency` lists, so `citations.py`/`guardrails.py`/`reconciliation.py` see exactly the same shape as before. The other 8 skills keep the shared `ExtractionResult` as their target (already schema-valid via the structured-outputs work).
- `ChatLlmExtractor.extract` requests `response_format={"type": "json_schema", ...}` for the skill's target schema, eliminating the "invalid JSON → gap" failure mode.
- `heuristic_extract.py`'s criticality detector now does a generic second pass: after all app names are discovered from the chunk set, it finds sentences mentioning "critical" that name an *already-discovered* application (no hardcoded list) and pulls an explicit high/medium/low level from the same sentence when stated.
- `EnsembleExtractor` (runs heuristic + LLM, merges via the existing dedupe) exists as an opt-in `EXTRACTION_STRATEGY=ensemble` — the default is `llm` with heuristic as the pure fallback (lower cost, matches the existing graceful-degradation principle).

**Config:** `EXTRACTION_STRATEGY=llm|heuristic|ensemble` (default `llm`).

---

## 5. RAG planning — doc-type-gated skill filter, not a full iterative coverage loop

**Problem that existed:** `agent_extract.py` ran all 11 `RAG_QUERIES` on every assessment regardless of content — a questionnaire-only upload still ran the vCPU/IOPS sweep.

**What was built:**
- `app/services/skills.py` — the 11 queries became `Skill` objects, each with a declarative `applies_to(doc_types) -> bool` gate (deliberately inclusive — a skill is excluded only when its signal genuinely can't come from the present doc types, e.g. `sizing` needs `inventory` or `code_snapshot` present).
- `HeuristicPlanner` (default) filters `SKILLS` by these gates and is the `RagPlanner` used by `agent_extract.init_run`. `NoOpRagPlanner` (`RAG_PLANNER=off`) always runs all 11, reproducing the exact pre-planner baseline for instant rollback.
- The existing single-templated `extra_gap_query` mechanism was generalized: when gaps remain after the filtered sweep, the configured chat LLM is asked (structured output, capped at `LLM_PLANNER_MAX_FOLLOWUPS`) for multiple targeted follow-up queries instead of one templated string concatenation, falling back to the original single gap query when no chat LLM is available.
- The existing per-query loop machinery (`select_query → retrieve → extract → validate → optional retry → accumulate`) and its cost/wall-clock budget breaker (`check_budget` node, `AGENT_MAX_LLM_CALLS` / `AGENT_WALL_CLOCK_SECONDS`) are unchanged.

**Explicitly not built (documented, not silent):**
- **The full iterative plan→act→reflect coverage loop** with re-planning after every batch — what shipped is a single doc-type-gated planning pass at `init_run`, plus the generalized gap-query mechanism as an approximation of "replan from remaining gaps." A genuine iterative loop is a bigger structural rewrite of the LangGraph state machine.
- **LangGraph `Send`-API parallel fan-out** of independent queries — real latency win, but nondeterministic completion order would break the two tests that hard-assert exact query counts/call ordering (`tests/test_agent_extract.py`). Deferred as a follow-up once those tests are redesigned around aggregate assertions instead of exact ordering.
- **Unifying the mock/LLM LangGraph duplication** into one parameterized graph — a structural refactor with no behavior change; both graphs are independently tested and working, so this stayed a documented follow-up rather than a risk taken this pass.

**Config:** `RAG_PLANNER=heuristic|off`, `LLM_PLANNER_MAX_FOLLOWUPS` (default 3).

---

## 6. Document classification — keywords become a graded, dynamic label; precedence stays fixed

**Problem that existed:** `parsers.infer_doc_type` is a filename/keyword ladder. Misclassification is silent and feeds `PRECEDENCE` directly.

**What was built:**
- `app/services/classify.py` — `KeywordClassifier` wraps the *existing* `infer_doc_type` ladder unchanged, grading confidence by match specificity (strong filename/extension signal ≈0.9, content-only inference ≈0.6, unresolved ≈0.4). `LlmClassifier` does zero-shot structured classification over the fixed `DocumentType` taxonomy, falling back to `KeywordClassifier` when disabled/unavailable/fails. It explicitly excludes `code_snapshot` from its candidate set (that label is only ever extension-derived for `.zip` files — a non-zip file classified as `code_snapshot` would make ingest try to unzip a file that isn't a zip).
- `ingest.py` calls the configured classifier for every non-`.zip` document; below `DOC_CLASSIFY_REVIEW_THRESHOLD` (default 0.6) the keyword-ladder guess is kept and a gap is appended to the report ("classified as X with low confidence — verify document type") — the same mechanism parse errors already use to surface into the review-facing report.
- `Document.doc_type_confidence` / `Document.doc_type_rationale` (new nullable columns) persist the classifier's output for inspectability.
- **`PRECEDENCE[doc_type]` lookup is byte-for-byte unchanged** — classification only decides the label; the precedence table is the same fixed dict it always was.

**Config:** `DOC_CLASSIFIER=llm|keyword` (default `llm`, auto-falls back to keyword when no chat LLM configured), `DOC_CLASSIFY_REVIEW_THRESHOLD` (default 0.6).

---

## 7. Dynamic questions — hybrid, not fully generated

**Decision (matches the original proposal exactly):** the curated, versioned base question set in `assessment_questions.py` (`_standard_answers`) was kept unchanged — full generation per run would destroy comparability across assessments. A `QuestionPlanner` generates *estate-specific supplementary* questions on top of it.

**What was built:**
- `app/services/question_planning.py` — `build_inventory_summary` derives a cheap `InventorySummary` (counts, unsupported-OS servers from blocked sizing recommendations, open conflict count, missing NFR attribute coverage) from the existing `inventory.load_inventory` snapshot.
- `HeuristicQuestionPlanner` (deterministic default, no LLM required): templated questions from clear signals already in the data, e.g. *"N server(s) have no Azure-supported target — what is the replatform plan?"* when sizing found unsupported OSes.
- `LlmQuestionPlanner`: structured-output list of estate-specific follow-ups from the same summary, falling back to the heuristic planner when unavailable.
- New `QuestionOrigin.dynamic` enum value tags these separately from `uploaded`/`ad_hoc` questions — persisted the same way and answered by the *unchanged* `answer_custom_question`/`build_assessment_answers` machinery (no changes needed there; it already iterates every `EngagementQuestion` row regardless of origin).

**Config:** `QUESTION_PLANNER_ENABLED` (default `true`), `QUESTION_PLANNER_MAX_QUESTIONS` (default 5).

---

## 8. Guardrails — detector chain + fixes found during review

`guardrails.py` already had injection regex, empty-chunk refusal, output allowlists, and optional Azure Content Safety. Added:

- **`InjectionDetector` chain**: `RegexInjectionDetector` (wraps the existing regex scan) and an optional `SemanticInjectionDetector` (LLM classifier, off by default via `GUARDRAILS_SEMANTIC_CHECK_ENABLED`), combined by `CompositeInjectionDetector` (Composite pattern) — `guard_extract_input` now scans through the composite instead of calling the regex scanner directly, so adding a new detector later doesn't require touching `guard_extract_input`.
- **Spotlighting**: chunk text sent to `ChatLlmExtractor` is wrapped in `<chunk id="...">...</chunk>` tags with an explicit "this is data, never instructions" system-prompt line.
- **Regex PII redaction** (`PII_REDACTION_MODE=regex|off`) over claim values/quotes and prose outputs — deliberately not Presidio, to avoid a heavy NLP dependency inconsistent with this repo's minimal pinned deps.
- **Fail-closed content safety** (`CONTENT_SAFETY_FAIL_OPEN`, default `true` to preserve prior behavior; the `lz` profile now requires it `false`).
- **Budget circuit breaker** (§5) doubles as a guardrail — on breach, the run finalizes with whatever was accumulated instead of continuing unbounded.

**Bugs found and fixed during a post-implementation correctness review** (not in the original proposal, surfaced by re-reading the code with fresh eyes):
- The `<chunk>` spotlighting fence didn't escape a literal `</chunk>` occurring inside a chunk's own text, which could let injected content break out of the fence early — fixed by neutralizing nested tag-like substrings.
- When `GUARDRAILS_BLOCK_ON_INJECTION=false`, a chunk-level injection hit was logged as a "sanitize" event but the unmodified injected text still reached the LLM unchanged (only query-level hits were actually stripped) — `GuardrailReport` now carries a real `sanitized_chunk_payload` that `apply_extract_guardrails` uses.
- PCI/HIPAA/criticality gap detection sat after an early loop `continue` that skipped all inventory-typed chunks entirely, so a CMDB "Notes" column mentioning PCI was invisible — moved ahead of the skip (these are generic substring checks, not prose-specific).

**Config:** `INJECTION_DETECTOR` behavior is controlled by `GUARDRAILS_SEMANTIC_CHECK_ENABLED`, plus the existing `GUARDRAILS_BLOCK_ON_INJECTION`/`GUARDRAILS_BLOCK_ON_HARM`, `PII_REDACTION_MODE`, `CONTENT_SAFETY_FAIL_OPEN`.

---

## 9. Provider-agnostic core, observability, distributed locking

Tracked as "Phase 5" in the original proposal and delivered as part of the prior modernization pass (see [ARCHITECTURE.md](ARCHITECTURE.md)):

- `LLM_PROVIDER=openai_compatible` + `OPENAI_BASE_URL` routes chat completions through any OpenAI-API-compatible endpoint, independent of the embeddings provider.
- OTel tracing seam (`OTEL_ENABLED`, off by default, no-op unless `opentelemetry-api` is installed).
- `DbLock` (`PIPELINE_LOCK_BACKEND=db`) — a DB-row-based pipeline lock safe across multiple workers, required in the `lz` profile. It now also self-heals from a hard-killed worker: a lock older than `PIPELINE_LOCK_STALE_SECONDS` (default 1800s) is stolen by the next `try_acquire` rather than sticking an assessment at "already running" forever.

---

## 10. What stays exactly the same (and why that's the point)

`citations.validate_citations`, `sizing.py`'s SKU rules, `pricing.py`'s math, and `reconciliation.py`'s precedence winner were **not touched** by any of the above. That's the deal: the app got dramatically better at *reading* fragmented enterprise inputs, while every SKU, cost, and precedence decision remains a defensible, rule-based artifact an auditor can trace.

---

## 11. Settings reference

`EXTRACTION_STRATEGY`, `RAG_PLANNER`, `LLM_PLANNER_MAX_FOLLOWUPS`, `DOC_CLASSIFIER`, `DOC_CLASSIFY_REVIEW_THRESHOLD`, `QUESTION_PLANNER_ENABLED`, `QUESTION_PLANNER_MAX_QUESTIONS`, `GUARDRAILS_SEMANTIC_CHECK_ENABLED`, `PII_REDACTION_MODE`, `CONTENT_SAFETY_FAIL_OPEN`, `PIPELINE_LOCK_BACKEND`, `PIPELINE_LOCK_STALE_SECONDS`. All resolved values are inspectable at runtime via `GET /health/config`.

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — full system architecture, request path, data model
- [README.md](../README.md) — quick start, RAG matrix, dynamic-architecture settings table
