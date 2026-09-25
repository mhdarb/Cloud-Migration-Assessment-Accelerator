"""Code-snapshot component resolution: generic placeholder names fold into the concrete
service they belong to.

Reported: "What apps are in scope?" answered "Container App, fleet-ingestor, Python App,
api, zephyr-order-api and Payments". The placeholders came from the manifest parsers, not the
LLM — and two Dockerfiles in two different services were blended into one "container-app".
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.services.code_manifests import GENERIC_APP_KEYS, process_code_snapshot
from app.services.extraction_merge import dedupe_extraction
from app.services.manifest_parsers.docker import BUILD_CONTEXT_ATTRIBUTE, parse_compose

SAMPLE = Path(__file__).resolve().parents[3] / "sample-data"


def _zip(tmp_path: Path, files: dict[str, str]) -> str:
    path = tmp_path / "snapshot.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return str(path)


def _apps(zip_path: str, doc_id: str = "d1") -> dict[str, dict[str, str]]:
    result = dedupe_extraction(process_code_snapshot(zip_path, "a1", doc_id).extraction)
    apps: dict[str, dict[str, str]] = {}
    for c in result.claims:
        if c.entity_type == "application":
            apps.setdefault(c.entity_key, {})[c.attribute] = c.value
    return apps


def _uses(zip_path: str, doc_id: str = "d1") -> set[str]:
    result = dedupe_extraction(process_code_snapshot(zip_path, "a1", doc_id).extraction)
    return {f"{d.source_key}->{d.target_key}" for d in result.dependencies}


# --------------------------------------------------------------------------- #
# The reported case (committed fixtures)
# --------------------------------------------------------------------------- #
def test_multi_service_portfolio_has_only_concrete_services(db_session):
    zip_path = str(SAMPLE / "stress" / "app-portfolio.zip")
    apps = _apps(zip_path)
    assert set(apps) == {"zephyr-order-api", "fleet-ingestor", "payments", "identity"}
    assert not set(apps) & GENERIC_APP_KEYS
    assert "api" not in apps  # compose service `api` with `build: .` IS zephyr-order-api


def test_facts_from_different_services_are_not_blended(db_session):
    """Both Dockerfiles used to become one 'container-app' mixing Node and Python facts."""
    apps = _apps(str(SAMPLE / "stress" / "app-portfolio.zip"))
    assert apps["zephyr-order-api"]["base_image"] == "node:20-alpine"
    assert apps["zephyr-order-api"]["port"] == "8080"
    assert apps["fleet-ingestor"]["base_image"] == "python:3.11-slim"
    assert apps["fleet-ingestor"]["framework"] == "fastapi"


def test_dependencies_attach_to_the_concrete_service(db_session):
    uses = _uses(str(SAMPLE / "stress" / "app-portfolio.zip"))
    assert {"fleet-ingestor->postgres", "fleet-ingestor->kafka", "payments->postgres"} <= uses
    assert not any(u.split("->")[0] in GENERIC_APP_KEYS for u in uses)


def test_single_service_snapshot_collapses_to_one_app(db_session):
    apps = _apps(str(SAMPLE / "sample-app.zip"))
    assert set(apps) == {"contoso-billing-api"}
    assert apps["contoso-billing-api"]["base_image"] == "node:18-alpine"  # from the Dockerfile


# --------------------------------------------------------------------------- #
# Resolution rules
# --------------------------------------------------------------------------- #
def test_directory_name_used_when_no_manifest_names_the_service(db_session, tmp_path):
    apps = _apps(_zip(tmp_path, {"billing-worker/Dockerfile": "FROM python:3.12\n"}))
    assert set(apps) == {"billing-worker"}
    assert apps["billing-worker"]["name"] == "billing-worker"


def test_generic_folders_defer_to_the_owning_service(db_session, tmp_path):
    apps = _apps(
        _zip(
            tmp_path,
            {
                "billing/package.json": '{"name": "billing-svc"}',
                "billing/docker/Dockerfile": "FROM node:20\nEXPOSE 3000\n",
            },
        )
    )
    assert set(apps) == {"billing-svc"}
    assert apps["billing-svc"]["port"] == "3000"


def test_compose_build_context_in_another_directory(db_session, tmp_path):
    apps = _apps(
        _zip(
            tmp_path,
            {
                "payments/Payments.csproj": "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>",
                "deploy/docker-compose.yml": "services:\n  pay:\n    build: ../payments\n",
            },
        )
    )
    assert "pay" not in apps and "payments" in apps


def test_unnamed_root_file_keeps_its_generic_name(db_session, tmp_path):
    """With nothing more specific anywhere, the placeholder is the honest answer."""
    apps = _apps(_zip(tmp_path, {"Dockerfile": "FROM node:20\n"}))
    assert set(apps) == {"container-app"}


def test_image_only_compose_services_stay_infrastructure(db_session, tmp_path):
    compose = "services:\n  web:\n    build: .\n  postgres:\n    image: postgres:16\n"
    result = process_code_snapshot(
        _zip(tmp_path, {"svc/package.json": '{"name": "svc"}', "svc/docker-compose.yml": compose}),
        "a1",
        "d1",
    ).extraction
    kinds = {(c.entity_type, c.entity_key) for c in result.claims if c.attribute == "name"}
    assert ("database", "postgres") in kinds
    assert ("application", "web") not in kinds and ("application", "svc") in kinds


def test_compose_parser_reads_both_build_forms():
    text = (
        "services:\n"
        "  api:\n    build: .\n"
        "  worker:\n    build:\n      context: ./worker\n"
        "  cache:\n    image: redis:7\n"
    )
    contexts = {
        c.entity_key: c.value for c in parse_compose(text, "x").claims if c.attribute == BUILD_CONTEXT_ATTRIBUTE
    }
    assert contexts == {"api": ".", "worker": "./worker"}


def test_build_context_is_never_persisted_as_a_fact(db_session):
    result = process_code_snapshot(str(SAMPLE / "sample-app.zip"), "a1", "d1").extraction
    assert not any(c.attribute == BUILD_CONTEXT_ATTRIBUTE for c in result.claims)
