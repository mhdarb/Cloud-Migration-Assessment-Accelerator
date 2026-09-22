from __future__ import annotations

import json
import math
import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import InfrastructureRecommendation, Server
from app.services.pricing import load_catalog, price_vm

UNSUPPORTED_OS_TOKENS = (
    "aix",
    "solaris",
    "hp-ux",
    "hpux",
    "z/os",
    "zos",
    "as/400",
    "os/400",
    "mainframe",
    "i-series",
    "os400",
)

MEASURED_FIELDS = (
    "vcpus",
    "memory_gb",
    "cpu_utilization_pct",
    "memory_utilization_pct",
    "disk_gb",
    "disk_iops",
    "disk_throughput_mbps",
)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def normalize_workload(server: Server) -> dict[str, Any]:
    attrs = server.attributes or {}
    aliases = {
        "vcpus": ("vcpus", "vcpu", "cpu_cores", "cores"),
        "memory_gb": ("memory_gb", "memory", "ram_gb", "ram"),
        "cpu_utilization_pct": (
            "cpu_utilization_pct",
            "cpu_utilization",
            "avg_cpu_pct",
            "cpu_pct",
        ),
        "memory_utilization_pct": (
            "memory_utilization_pct",
            "memory_utilization",
            "avg_memory_pct",
            "memory_pct",
        ),
        "disk_gb": ("disk_gb", "storage_gb", "disk_capacity_gb", "storage"),
        "disk_iops": ("disk_iops", "iops"),
        "disk_throughput_mbps": ("disk_throughput_mbps", "throughput_mbps"),
    }
    normalized: dict[str, Any] = {
        "server": server.name,
        "server_key": server.normalized_key,
        "os": attrs.get("os", "unknown"),
        "architecture": str(attrs.get("architecture", "x64")).lower(),
        "environment": str(attrs.get("environment", "production")).lower(),
        "raw": attrs,
        "evidence_confidence": server.confidence,
    }
    for target, names in aliases.items():
        normalized[target] = next(
            (_number(attrs[name]) for name in names if _number(attrs.get(name)) is not None),
            None,
        )
    return normalized


def _with_assumptions(profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    defaults = {
        "vcpus": (2.0, "Current vCPU count missing; assumed 2 vCPUs."),
        "memory_gb": (8.0, "Current memory missing; assumed 8 GB."),
        "cpu_utilization_pct": (
            65.0,
            "CPU utilization missing; assumed 65% sustained utilization.",
        ),
        "memory_utilization_pct": (
            65.0,
            "Memory utilization missing; assumed 65% sustained utilization.",
        ),
        "disk_gb": (128.0, "Disk capacity missing; assumed 128 GB."),
        "disk_iops": (500.0, "Disk IOPS missing; assumed 500 IOPS."),
        "disk_throughput_mbps": (
            60.0,
            "Disk throughput missing; assumed 60 MB/s.",
        ),
    }
    result = dict(profile)
    assumptions: list[str] = []
    assumed_fields = [key for key, (value, note) in defaults.items() if result.get(key) is None]
    measured_fields = [key for key in MEASURED_FIELDS if result.get(key) is not None]
    for key, (value, note) in defaults.items():
        if result.get(key) is None:
            result[key] = value
            assumptions.append(note)
    result["assumed_fields"] = assumed_fields
    result["measured_fields"] = measured_fields
    return result, assumptions


def _explanation(result: dict[str, Any]) -> tuple[str, str]:
    facts = (
        f"{result['server']} requires at least {result['required']['vcpus']} vCPUs, "
        f"{result['required']['memory_gb']} GB RAM and disk meeting "
        f"{result['required']['disk_iops']} IOPS."
    )
    disk_name = (result.get("disk") or {}).get("name") or "the selected disk"
    fallback = (
        f"{facts} {result['recommended_sku']} is the lowest-cost compatible catalog "
        f"candidate and {disk_name} satisfies capacity and performance rules."
    )
    from app.services.llm_clients import get_chat_completer
    from app.services.llm_prompts import SIZING_EXPLAIN_SYSTEM

    completer = get_chat_completer()
    if not completer.enabled:
        return fallback, "deterministic-template"
    text = completer.complete(
        SIZING_EXPLAIN_SYSTEM, json.dumps(result), temperature=0
    )
    if not text:
        return fallback, "deterministic-template-fallback"
    return text, completer.source


def _os_supported(os_name: str) -> bool:
    lowered = str(os_name or "unknown").lower()
    return not any(token in lowered for token in UNSUPPORTED_OS_TOKENS)


def _blocked_result(
    profile: dict[str, Any],
    *,
    reason: str,
    assumptions: list[str],
    catalog_version: str,
    region: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    empty_price = {
        "monthly_compute": 0,
        "monthly_disk": 0,
        "monthly_total": 0,
        "currency": get_settings().pricing_currency,
        "source": "none",
        "estimate": True,
    }
    return {
        "server": profile["server"],
        "server_key": profile["server_key"],
        "provider": "azure",
        "region": region,
        "catalog_version": catalog_version,
        "sku_decision": "blocked",
        "recommended_sku": "",
        "vm": {},
        "disk": {},
        "alternatives": [],
        "rejected_candidates": [],
        "required": {},
        "input_profile": profile,
        "assumptions": assumptions,
        "measured_fields": profile.get("measured_fields") or [],
        "assumed_fields": profile.get("assumed_fields") or [],
        "compatibility_checks": checks,
        "pricing": empty_price,
        "cost_optimization": [],
        "confidence": 0.2,
        "needs_human_review": True,
        "explanation": reason,
        "explanation_source": "deterministic-template",
    }


def size_profile(profile: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    catalog = load_catalog()
    p, assumptions = _with_assumptions(profile)
    os_supported = _os_supported(p["os"])
    if not os_supported:
        return _blocked_result(
            p,
            reason=(
                f"{p['server']} runs {p['os']}, which is outside the Azure IaaS catalog. "
                "No VM SKU is recommended."
            ),
            assumptions=assumptions,
            catalog_version=catalog["version"],
            region=settings.sizing_region,
            checks=[
                {
                    "check": "operating_system",
                    "status": "fail",
                    "detail": p["os"],
                }
            ],
        )
    cpu_required = max(1, math.ceil(p["vcpus"] * p["cpu_utilization_pct"] / 100 / 0.7 * 1.2))
    memory_required = max(
        1, math.ceil(p["memory_gb"] * p["memory_utilization_pct"] / 100 / 0.7 * 1.2)
    )
    architecture = "arm64" if "arm" in p["architecture"] else "x64"
    production = p["environment"] in {"prod", "production"}
    rejected: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for vm in catalog["vms"]:
        reasons = []
        if vm["vcpus"] < cpu_required:
            reasons.append("insufficient vCPU")
        if vm["memory_gb"] < memory_required:
            reasons.append("insufficient memory")
        if architecture not in vm["architectures"]:
            reasons.append(f"{architecture} architecture unsupported")
        if production and vm["family"] == "burstable":
            reasons.append("burstable family excluded for production")
        priced = {**vm, "price": price_vm(vm, settings.sizing_region, settings.pricing_currency)}
        if reasons:
            rejected.append({"sku": vm["name"], "reasons": reasons})
        else:
            eligible.append(priced)
    eligible.sort(key=lambda vm: vm["price"]["monthly"])
    if not eligible:
        return _blocked_result(
            p,
            reason=f"No catalog SKU satisfies workload {p['server']}.",
            assumptions=assumptions,
            catalog_version=catalog["version"],
            region=settings.sizing_region,
            checks=[
                {"check": "capacity", "status": "fail", "detail": "no eligible VM in catalog"}
            ],
        )

    premium = (
        p["disk_iops"] > 500
        or p["disk_throughput_mbps"] > 60
        or p["environment"] in {"prod", "production"}
    )
    disk_type = "Premium SSD" if premium else "Standard SSD"
    disks = [
        d
        for d in catalog["disks"]
        if d["type"] == disk_type
        and d["capacity_gb"] >= p["disk_gb"]
        and d["iops"] >= p["disk_iops"]
        and d["throughput_mbps"] >= p["disk_throughput_mbps"]
    ]
    disk_satisfied = bool(disks)
    if not disks:
        disks = [d for d in catalog["disks"] if d["type"] == "Premium SSD"]
    disk = (
        sorted(disks, key=lambda d: d["monthly"])[0]
        if disk_satisfied
        else sorted(disks, key=lambda d: d["capacity_gb"], reverse=True)[0]
    )
    primary = eligible[0]
    total = round(primary["price"]["monthly"] + disk["monthly"], 2)
    confidence = max(0.35, round(p["evidence_confidence"] - 0.06 * len(assumptions), 2))
    result = {
        "server": p["server"],
        "server_key": p["server_key"],
        "provider": "azure",
        "region": settings.sizing_region,
        "catalog_version": catalog["version"],
        "sku_decision": "recommended",
        "recommended_sku": primary["name"],
        "vm": primary,
        "disk": disk,
        "alternatives": eligible[1:4],
        "rejected_candidates": rejected,
        "required": {
            "vcpus": cpu_required,
            "memory_gb": memory_required,
            "disk_gb": p["disk_gb"],
            "disk_iops": p["disk_iops"],
            "disk_throughput_mbps": p["disk_throughput_mbps"],
        },
        "input_profile": p,
        "assumptions": assumptions,
        "measured_fields": p.get("measured_fields") or [],
        "assumed_fields": p.get("assumed_fields") or [],
        "compatibility_checks": [
            {"check": "architecture", "status": "pass", "detail": architecture},
            {
                "check": "operating_system",
                "status": "pass" if p["os"] != "unknown" else "review",
                "detail": p["os"],
            },
            {"check": "capacity", "status": "pass", "detail": "20% headroom at 70% target utilization"},
            {
                "check": "disk_performance",
                "status": "pass" if disk_satisfied else "review",
                "detail": "catalog match" if disk_satisfied else "requirements exceed local catalog",
            },
        ],
        "pricing": {
            "monthly_compute": primary["price"]["monthly"],
            "monthly_disk": disk["monthly"],
            "monthly_total": total,
            "currency": settings.pricing_currency,
            "source": primary["price"]["source"],
            "estimate": True,
        },
        "cost_optimization": [
            {
                "type": "reservation_candidate",
                "applicable": p["environment"] in {"prod", "production"},
                "detail": "Validate one- or three-year reservation after workload stability is confirmed.",
            },
            {
                "type": "runtime_schedule",
                "applicable": p["environment"] not in {"prod", "production"},
                "detail": "Consider shutdown schedules for non-production workloads.",
            },
        ],
        "confidence": confidence,
        "needs_human_review": bool(assumptions)
        or p["os"] == "unknown"
        or not disk_satisfied,
    }
    result["explanation"], result["explanation_source"] = _explanation(result)
    return result


def generate_recommendations(db: Session, assessment_id: str) -> list[InfrastructureRecommendation]:
    db.query(InfrastructureRecommendation).filter(
        InfrastructureRecommendation.assessment_id == assessment_id
    ).delete(synchronize_session=False)
    rows: list[InfrastructureRecommendation] = []
    servers = db.query(Server).filter(Server.assessment_id == assessment_id).all()
    for server in servers:
        try:
            result = size_profile(normalize_workload(server))
        except Exception as exc:
            result = _blocked_result(
                {
                    "server": server.name,
                    "server_key": server.normalized_key,
                    "assumed_fields": [],
                    "measured_fields": [],
                },
                reason=f"Sizing failed for {server.name}: {exc}",
                assumptions=[],
                catalog_version=load_catalog()["version"],
                region=get_settings().sizing_region,
                checks=[{"check": "engine", "status": "fail", "detail": str(exc)}],
            )
        row = InfrastructureRecommendation(
            assessment_id=assessment_id,
            server_key=server.normalized_key,
            region=result["region"],
            recommended_sku=result.get("recommended_sku") or "none",
            result=result,
            confidence=result["confidence"],
            needs_human_review=result["needs_human_review"],
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows
