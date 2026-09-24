#!/usr/bin/env python3
"""Run the RAG/extraction eval harness over a labeled estate and print the report.

This is the measurement tool for quality changes: run it, flip one variable (turn the
reranker on, swap the embedder, enable contextual retrieval), re-run, and compare the
numbers. By default it uses the deterministic mock/heuristic path; pass --real to use the
configured LLM + embeddings (needs credentials / MOCK_LLM=false).

    python scripts/run_eval.py                 # deterministic, Contoso estate
    python scripts/run_eval.py --real          # configured LLM + embeddings
    python scripts/run_eval.py --json          # machine-readable report

Runs in-process against a throwaway SQLite DB; nothing touches your real data.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))
SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample-data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the eval harness over a labeled estate.")
    parser.add_argument("--estate", default="contoso", help="labeled estate name (default: contoso)")
    parser.add_argument("--real", action="store_true", help="use the configured LLM + embeddings")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    import os

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{tmp}/eval.db"
        os.environ["STORAGE_DIR"] = f"{tmp}/uploads"
        os.environ["EMBEDDINGS_DIR"] = f"{tmp}/embeddings"
        os.environ["MOCK_LLM"] = "false" if args.real else "true"
        os.environ.setdefault("LOCAL_EMBEDDINGS", "true")

        from app.config import get_settings

        get_settings.cache_clear()
        from app.database import SessionLocal, init_db
        from app.models.entities import Assessment, Document, DocumentType, PipelineStatus
        from app.services.eval_datasets import ALL_ESTATES
        from app.services.eval_harness import evaluate, run_pipeline

        labels = ALL_ESTATES.get(args.estate)
        if labels is None:
            print(f"unknown estate '{args.estate}'; known: {', '.join(ALL_ESTATES)}", file=sys.stderr)
            return 2

        init_db()
        session = SessionLocal()
        try:
            row = Assessment(name=f"eval-{labels.name}", status=PipelineStatus.pending)
            session.add(row)
            session.commit()
            session.refresh(row)
            assessment_id = row.id
            for filename, content_type in labels.files:
                session.add(
                    Document(
                        assessment_id=assessment_id,
                        filename=filename,
                        content_type=content_type,
                        storage_path=str(SAMPLE_DATA / filename),
                        doc_type=DocumentType.unknown,
                    )
                )
            session.commit()

            retriever = run_pipeline(session, assessment_id, use_mock=not args.real)
            report = evaluate(
                session, assessment_id, labels, retriever, top_k=get_settings().rag_top_k
            )
        finally:
            session.close()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.format_table())
        print("\nper-probe:")
        for row in report.per_probe:
            print(f"  {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
