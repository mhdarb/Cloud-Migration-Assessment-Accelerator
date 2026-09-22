#!/usr/bin/env python3
"""End-to-end smoke for grounded assessment answers and Azure sizing outputs."""

from __future__ import annotations

import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
SAMPLES = [
    "architecture-overview.docx",
    "cmdb-inventory.xlsx",
    "assessment-questionnaire.docx",
    "requirements-nfr.docx",
    "sample-app.zip",
]


def main() -> None:
    with httpx.Client(timeout=60) as client:
        files = [
            (
                "files",
                (name, (ROOT / "sample-data" / name).read_bytes(), "application/octet-stream"),
            )
            for name in SAMPLES
        ]
        response = client.post(
            f"{API}/assessments", data={"name": "Core capabilities smoke"}, files=files
        )
        response.raise_for_status()
        assessment = response.json()
        assessment_id = assessment["id"]

        for _ in range(120):
            current = client.get(f"{API}/assessments/{assessment_id}").json()
            if current["status"] in {"completed", "failed"}:
                break
            time.sleep(1)
        assert current["status"] == "completed", current.get("error_message")

        questions = client.get(
            f"{API}/assessments/{assessment_id}/assessment-questions"
        ).json()
        recommendations = client.get(
            f"{API}/assessments/{assessment_id}/recommendations"
        ).json()
        report = client.get(f"{API}/assessments/{assessment_id}/report").json()

        assert questions["question_set"] == "migration-readiness-v1"
        assert any(answer["evidence_refs"] for answer in questions["answers"])
        assert recommendations
        assert all(
            item["result"].get("sku_decision") == "blocked"
            or item["result"]["pricing"]["monthly_total"] > 0
            for item in recommendations
        )
        assert report["report_json"]["assessment_questions"]
        assert report["report_json"]["infrastructure_recommendations"]
        print(
            f"PASS assessment={assessment_id} "
            f"answers={len(questions['answers'])} recommendations={len(recommendations)}"
        )


if __name__ == "__main__":
    main()
