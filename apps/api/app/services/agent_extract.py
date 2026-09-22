"""LangGraph harness for RAG extraction (Implement-stage AI orchestration).

Outer pipeline stays FastAPI → pipeline.py. This graph owns:
retrieve → extract → validate citations → optional retry → next query → finalize.
"""

from __future__ import annotations

import json
import logging
import operator
import time
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import Chunk, Document, DocumentType
from app.schemas.api import ExtractedClaim, ExtractedDependency, ExtractionResult
from app.schemas.chunking import ChunkPayload
from app.schemas.planning import CorpusProfile
from app.services.citations import validate_citations
from app.services.compat import call_if_supported
from app.services.extraction_merge import dedupe_extraction
from app.services.guardrails import apply_extract_guardrails, sanitize_extraction_output
from app.services.heuristic_extract import chunks_from_payload, chunks_to_payload, heuristic_extract
from app.services.ports import LlmExtractor, Retriever
from app.services.skills import RAG_QUERIES, SKILLS_BY_NAME, SKILLS_BY_QUERY, HeuristicPlanner, NoOpRagPlanner

logger = logging.getLogger(__name__)

MAX_RETRIES_PER_QUERY = 1


def get_rag_planner(mode: str):
    """Small factory, mirrors `providers.py`'s composition-root style for this
    module-local port (RagPlanner) since it's only ever consumed inside this graph."""
    if mode == "off":
        return NoOpRagPlanner()
    return HeuristicPlanner()


class _FollowUpQueries(BaseModel):
    queries: list[str] = []


def propose_followup_queries(gaps: list[str], max_n: int) -> list[str]:
    """LlmPlanner: ask the configured chat LLM for targeted follow-up retrieval queries
    from remaining gaps. Returns [] when no chat LLM is configured/enabled/fails —
    callers fall back to the single templated `extra_gap_query` in that case."""
    from app.services.llm_clients import get_chat_completer

    if not gaps or max_n <= 0:
        return []
    completer = get_chat_completer()
    if not completer.enabled:
        return []
    system = (
        "You plan retrieval queries for a cloud migration assessment RAG pipeline. "
        "Given evidence gaps, propose short, targeted retrieval queries (not questions "
        "addressed to a person) likely to surface grounding evidence for them."
    )
    user = "Gaps:\n" + "\n".join(f"- {g}" for g in gaps[:20])
    content = call_if_supported(
        completer.complete,
        system,
        user,
        temperature=0.2,
        json_mode=True,
        response_schema=_FollowUpQueries,
    )
    if not content:
        return []
    try:
        parsed = _FollowUpQueries.model_validate(json.loads(content))
    except Exception:
        return []
    unique = list(dict.fromkeys(q.strip() for q in parsed.queries if q and q.strip()))
    return unique[:max_n]


class ExtractGraphState(TypedDict, total=False):
    assessment_id: str
    query_index: int
    queries: list[str]
    current_query: str
    retry_count: int
    chunk_ids: list[str]
    chunk_texts: dict[str, str]
    chunk_payload: list[dict[str, Any]]
    partial_claims: list[dict[str, Any]]
    partial_deps: list[dict[str, Any]]
    partial_gaps: list[str]
    partial_assumptions: list[str]
    needs_retry: bool
    # Accumulated across queries (reducer)
    claims: Annotated[list[dict[str, Any]], operator.add]
    dependencies: Annotated[list[dict[str, Any]], operator.add]
    gaps: Annotated[list[str], operator.add]
    assumptions: Annotated[list[str], operator.add]
    all_retrieved_ids: Annotated[list[str], operator.add]
    retries_used: Annotated[list[int], operator.add]
    node_trace: Annotated[list[str], operator.add]
    guardrail_blocks: Annotated[list[int], operator.add]
    guardrail_sanitizes: Annotated[list[int], operator.add]
    guardrail_events: Annotated[list[int], operator.add]
    gap_query_added: bool
    run_started_at: float
    llm_calls: Annotated[list[int], operator.add]
    budget_exceeded: bool
    current_skill_name: str | None


def _result_to_parts(result: ExtractionResult) -> dict[str, Any]:
    return {
        "partial_claims": [c.model_dump() for c in result.claims],
        "partial_deps": [d.model_dump() for d in result.dependencies],
        "partial_gaps": list(result.gaps),
        "partial_assumptions": list(result.assumptions),
    }


def _parts_to_result(state: ExtractGraphState) -> ExtractionResult:
    return ExtractionResult(
        claims=[ExtractedClaim.model_validate(c) for c in state.get("partial_claims") or []],
        dependencies=[
            ExtractedDependency.model_validate(d) for d in state.get("partial_deps") or []
        ],
        gaps=list(state.get("partial_gaps") or []),
        assumptions=list(state.get("partial_assumptions") or []),
    )


def _effective_queries(state: ExtractGraphState) -> list[str]:
    """`RAG_QUERIES` is only a fallback for "never set" (`None`) — a planner that
    legitimately resolves to an empty list (no applicable skills) must stay empty,
    not silently revert to running everything (empty list is falsy in Python, so a
    plain `state.get("queries") or RAG_QUERIES` would mask that decision)."""
    queries = state.get("queries")
    return queries if queries is not None else RAG_QUERIES


def _ungrounded_ratio(result: ExtractionResult) -> float:
    items = list(result.claims) + list(result.dependencies)
    if not items:
        return 0.0
    bad = sum(1 for x in items if not x.chunk_ids)
    return bad / len(items)


def _boost_chunks(db: Session, assessment_id: str) -> list[Chunk]:
    return (
        db.query(Chunk)
        .join(Document, Document.id == Chunk.document_id)
        .filter(
            Chunk.assessment_id == assessment_id,
            Document.doc_type.in_(
                [
                    DocumentType.inventory,
                    DocumentType.requirements,
                    DocumentType.code_snapshot,
                ]
            ),
        )
        .all()
    )


def _payload_to_chunks(
    assessment_id: str, payload: list[ChunkPayload]
) -> list[Chunk]:
    return chunks_from_payload(payload, assessment_id)


def _extract_node_out(result: ExtractionResult, report: Any) -> dict[str, Any]:
    return {
        **_result_to_parts(result),
        "guardrail_blocks": [1] if not report.allowed else [],
        "guardrail_sanitizes": [sum(1 for e in report.events if e.action == "sanitize")],
        "guardrail_events": [len(report.events)],
        "node_trace": [
            "extract:guardrails_block" if not report.allowed else "extract:guardrails_ok"
        ],
    }


GAP_QUERY_PREFIX = "Find evidence for remaining assessment gaps: "
GAP_QUERY_MAX_CHARS = 400


def extra_gap_query(gaps: list[str]) -> str | None:
    unique = list(dict.fromkeys(g.strip() for g in gaps if g and g.strip()))
    if not unique:
        return None
    return f"{GAP_QUERY_PREFIX}{'; '.join(unique)[:GAP_QUERY_MAX_CHARS]}"


def build_mock_extract_graph(db: Session, docs: dict[str, Document], _retriever: Retriever):
    """Batch retrieve-all → heuristic extract → validate (offline demo path)."""

    def retrieve_all(state: ExtractGraphState) -> dict[str, Any]:
        retrieved: dict[str, Chunk] = {
            c.id: c
            for c in db.query(Chunk)
            .filter(Chunk.assessment_id == state["assessment_id"])
            .all()
        }
        for c in _boost_chunks(db, state["assessment_id"]):
            retrieved[c.id] = c
        chunks = list(retrieved.values())
        payload = chunks_to_payload(chunks)
        return {
            "chunk_ids": list(retrieved.keys()),
            "chunk_texts": {cid: c.text for cid, c in retrieved.items()},
            "chunk_payload": payload,
            "all_retrieved_ids": list(retrieved.keys()),
            "current_query": "batch_heuristic",
            "node_trace": [f"retrieve_all:n={len(chunks)}"],
        }

    def extract(state: ExtractGraphState) -> dict[str, Any]:
        payload = state.get("chunk_payload") or []

        def _run(_query: str, _payload: list[ChunkPayload]) -> ExtractionResult:
            return heuristic_extract(
                _payload_to_chunks(state["assessment_id"], _payload), docs
            )

        result, report = apply_extract_guardrails(
            state.get("current_query") or "batch_heuristic",
            payload,
            _run,
        )
        return _extract_node_out(result, report)

    def validate(state: ExtractGraphState) -> dict[str, Any]:
        result = _parts_to_result(state)
        result = validate_citations(
            result, set(state.get("chunk_ids") or []), state.get("chunk_texts") or {}
        )
        result, _ = sanitize_extraction_output(result)
        return {
            **_result_to_parts(result),
            "claims": [c.model_dump() for c in result.claims],
            "dependencies": [d.model_dump() for d in result.dependencies],
            "gaps": list(result.gaps),
            "assumptions": list(result.assumptions),
            "node_trace": ["validate"],
        }

    def finalize(state: ExtractGraphState) -> dict[str, Any]:
        return {"node_trace": ["finalize"]}

    graph = StateGraph(ExtractGraphState)
    graph.add_node("retrieve_all", retrieve_all)
    graph.add_node("extract", extract)
    graph.add_node("validate", validate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "retrieve_all")
    graph.add_edge("retrieve_all", "extract")
    graph.add_edge("extract", "validate")
    graph.add_edge("validate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def build_llm_extract_graph(
    db: Session, docs: dict[str, Document], retriever: Retriever, llm: LlmExtractor
):
    """Per-query retrieve → LLM extract → validate → optional citation retry."""
    settings = get_settings()

    def init_run(state: ExtractGraphState) -> dict[str, Any]:
        queries = list(state.get("queries") or [])
        if not queries:
            doc_types = {d.doc_type for d in docs.values()} if docs else set()
            profile = CorpusProfile(doc_types=doc_types, document_count=len(docs))
            planner = get_rag_planner(settings.rag_planner)
            queries = planner.plan(profile).as_strings()
        return {
            "queries": queries,
            "query_index": 0,
            "retry_count": 0,
            "needs_retry": False,
            "run_started_at": time.monotonic(),
            "llm_calls": [],
            "budget_exceeded": False,
            "node_trace": ["init"],
        }

    def select_query(state: ExtractGraphState) -> dict[str, Any]:
        idx = int(state.get("query_index") or 0)
        queries = _effective_queries(state)
        if idx >= len(queries):
            return {"current_query": "", "current_skill_name": None, "node_trace": ["select_query:done"]}
        base_q = queries[idx]
        skill = SKILLS_BY_QUERY.get(base_q)
        q = base_q
        if int(state.get("retry_count") or 0) > 0:
            q = f"{q} — cite exact evidence quotes from the retrieved chunks"
        return {
            "current_query": q,
            "current_skill_name": skill.name if skill else None,
            "needs_retry": False,
            "partial_claims": [],
            "partial_deps": [],
            "partial_gaps": [],
            "partial_assumptions": [],
            "node_trace": [f"select_query:{idx}"],
        }

    def retrieve(state: ExtractGraphState) -> dict[str, Any]:
        query = state.get("current_query") or ""
        if not query:
            return {
                "chunk_ids": [],
                "chunk_texts": {},
                "chunk_payload": [],
                "node_trace": ["retrieve:skip"],
            }
        chunks = retriever.retrieve(
            db, state["assessment_id"], query, top_k=settings.rag_top_k
        )
        texts = {c.id: c.text for c in chunks}
        payload = chunks_to_payload(chunks)
        return {
            "chunk_ids": [c.id for c in chunks],
            "chunk_texts": texts,
            "chunk_payload": payload,
            "all_retrieved_ids": [c.id for c in chunks],
            "node_trace": [f"retrieve:n={len(chunks)}"],
        }

    def extract(state: ExtractGraphState) -> dict[str, Any]:
        query = state.get("current_query") or ""
        payload = state.get("chunk_payload") or []
        skill = SKILLS_BY_NAME.get(state.get("current_skill_name") or "")
        target_schema = skill.target_schema if skill else None
        system_prompt_suffix = skill.system_prompt if skill else None

        def _run(q: str, p: list[ChunkPayload]) -> ExtractionResult:
            try:
                return call_if_supported(
                    llm.extract,
                    q,
                    p,
                    target_schema=target_schema,
                    system_prompt_suffix=system_prompt_suffix,
                )
            except Exception:
                logger.exception("Extract node failed for query: %s", q)
                return heuristic_extract(
                    _payload_to_chunks(state["assessment_id"], p), docs
                )

        result, report = apply_extract_guardrails(query, payload, _run)
        return {**_extract_node_out(result, report), "llm_calls": [1]}

    def validate(state: ExtractGraphState) -> dict[str, Any]:
        result = _parts_to_result(state)
        texts = state.get("chunk_texts") or {}
        allowed = set(state.get("chunk_ids") or [])
        result = validate_citations(result, allowed, texts)
        result, _ = sanitize_extraction_output(result)
        ratio = _ungrounded_ratio(result)
        retry_count = int(state.get("retry_count") or 0)
        needs = ratio >= 0.5 and retry_count < MAX_RETRIES_PER_QUERY and bool(allowed)
        if (
            result.claims
            and all(not c.chunk_ids for c in result.claims)
            and retry_count < MAX_RETRIES_PER_QUERY
        ):
            needs = True
        return {
            **_result_to_parts(result),
            "needs_retry": needs,
            "node_trace": [f"validate:ungrounded={ratio:.2f}:retry={needs}"],
        }

    def prepare_retry(state: ExtractGraphState) -> dict[str, Any]:
        return {
            "retry_count": int(state.get("retry_count") or 0) + 1,
            "retries_used": [1],
            "node_trace": ["prepare_retry"],
        }

    def accumulate(state: ExtractGraphState) -> dict[str, Any]:
        queries = list(_effective_queries(state))
        next_idx = int(state.get("query_index") or 0) + 1
        extra: dict[str, Any] = {}
        if next_idx >= len(queries) and not state.get("gap_query_added"):
            all_gaps = list(state.get("gaps") or []) + list(state.get("partial_gaps") or [])
            followups: list[str] = []
            if settings.rag_planner != "off":
                followups = propose_followup_queries(all_gaps, settings.llm_planner_max_followups)
            if not followups:
                gap_q = extra_gap_query(all_gaps)
                followups = [gap_q] if gap_q else []
            if followups:
                queries.extend(followups)
                extra["queries"] = queries
                extra["gap_query_added"] = True
                extra["node_trace"] = ["accumulate", f"gap_queries:{len(followups)}"]
        return {
            "claims": list(state.get("partial_claims") or []),
            "dependencies": list(state.get("partial_deps") or []),
            "gaps": list(state.get("partial_gaps") or []),
            "assumptions": list(state.get("partial_assumptions") or []),
            "query_index": next_idx,
            "retry_count": 0,
            "needs_retry": False,
            "node_trace": extra.get("node_trace") or ["accumulate"],
            **{k: v for k, v in extra.items() if k != "node_trace"},
        }

    def route_after_validate(
        state: ExtractGraphState,
    ) -> Literal["prepare_retry", "accumulate"]:
        if state.get("needs_retry"):
            return "prepare_retry"
        return "accumulate"

    def route_after_accumulate(
        state: ExtractGraphState,
    ) -> Literal["select_query", "finalize"]:
        idx = int(state.get("query_index") or 0)
        queries = _effective_queries(state)
        if idx < len(queries):
            return "select_query"
        return "finalize"

    def check_budget(state: ExtractGraphState) -> dict[str, Any]:
        calls = sum(state.get("llm_calls") or [])
        started = state.get("run_started_at") or time.monotonic()
        elapsed = time.monotonic() - started
        exceeded = calls >= settings.agent_max_llm_calls or elapsed >= settings.agent_wall_clock_seconds
        return {
            "budget_exceeded": exceeded,
            "node_trace": [f"check_budget:calls={calls}:elapsed={elapsed:.1f}:exceeded={exceeded}"],
        }

    def route_after_budget(
        state: ExtractGraphState,
    ) -> Literal["select_query", "finalize"]:
        if state.get("budget_exceeded"):
            return "finalize"
        return route_after_accumulate(state)

    def finalize(state: ExtractGraphState) -> dict[str, Any]:
        return {"node_trace": ["finalize"]}

    graph = StateGraph(ExtractGraphState)
    graph.add_node("init_run", init_run)
    graph.add_node("select_query", select_query)
    graph.add_node("retrieve", retrieve)
    graph.add_node("extract", extract)
    graph.add_node("validate", validate)
    graph.add_node("prepare_retry", prepare_retry)
    graph.add_node("accumulate", accumulate)
    graph.add_node("check_budget", check_budget)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "init_run")
    graph.add_edge("init_run", "select_query")
    graph.add_edge("select_query", "retrieve")
    graph.add_edge("retrieve", "extract")
    graph.add_edge("extract", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"prepare_retry": "prepare_retry", "accumulate": "accumulate"},
    )
    graph.add_edge("prepare_retry", "select_query")
    graph.add_edge("accumulate", "check_budget")
    graph.add_conditional_edges(
        "check_budget",
        route_after_budget,
        {"select_query": "select_query", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def _dedupe_result(result: ExtractionResult) -> ExtractionResult:
    return dedupe_extraction(
        result,
        default_assumption="Extracted via LangGraph RAG harness; validate before migration planning",
    )


def run_extract_agent(
    db: Session,
    assessment_id: str,
    docs: dict[str, Document],
    *,
    retriever: Retriever,
    llm: LlmExtractor,
    use_mock: bool,
) -> tuple[ExtractionResult, dict[str, Any]]:
    """Execute LangGraph RAG extract; same I/O contract as legacy extractors."""
    app = (
        build_mock_extract_graph(db, docs, retriever)
        if use_mock
        else build_llm_extract_graph(db, docs, retriever, llm)
    )
    initial: ExtractGraphState = {
        "assessment_id": assessment_id,
        "query_index": 0,
        "retry_count": 0,
        "claims": [],
        "dependencies": [],
        "gaps": [],
        "assumptions": [],
        "all_retrieved_ids": [],
        "retries_used": [],
        "node_trace": [],
        "guardrail_blocks": [],
        "guardrail_sanitizes": [],
        "guardrail_events": [],
        "gap_query_added": False,
        "llm_calls": [],
        "budget_exceeded": False,
    }
    # `init_run` resolves `queries` from the planner (see skills.resolve_queries) when
    # the caller doesn't supply one; the mock graph doesn't use `queries` at all.
    # Per-query LLM path is ~5 nodes × (queries + optional gap query + retries).
    final = app.invoke(initial, {"recursion_limit": 250})
    result = ExtractionResult(
        claims=[ExtractedClaim.model_validate(c) for c in final.get("claims") or []],
        dependencies=[
            ExtractedDependency.model_validate(d) for d in final.get("dependencies") or []
        ],
        gaps=list(dict.fromkeys(final.get("gaps") or [])),
        assumptions=list(dict.fromkeys(final.get("assumptions") or [])),
    )
    result = _dedupe_result(result)
    retrieved = list(dict.fromkeys(final.get("all_retrieved_ids") or []))
    retries = sum(final.get("retries_used") or [])
    final_queries = final.get("queries")
    metrics = {
        "rag_enabled": True,
        "rag_queries": len(final_queries if final_queries is not None else RAG_QUERIES),
        "chunks_retrieved": len(retrieved),
        "retrieval_mode": "langgraph_heuristic_rag" if use_mock else "langgraph_llm_rag",
        "agent_harness": "langgraph",
        "agent_retries": retries,
        "agent_nodes": len(final.get("node_trace") or []),
        "guardrails": True,
        "guardrail_blocks": sum(final.get("guardrail_blocks") or []),
        "guardrail_sanitizes": sum(final.get("guardrail_sanitizes") or []),
        "guardrail_events": sum(final.get("guardrail_events") or []),
        "budget_exceeded": bool(final.get("budget_exceeded")),
    }
    return result, metrics
