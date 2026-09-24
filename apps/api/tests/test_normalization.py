"""Canonical unit normalization for heterogeneous infrastructure inventory."""

from __future__ import annotations

import pytest

from app.models.entities import Server
from app.services.normalization import (
    canonical_header,
    comparison_value,
    is_plausible,
    to_canonical,
)
from app.services.sizing import normalize_workload, size_profile


# --------------------------------------------------------------------------- #
# Unit conversion to canonical fields
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field,raw,expected",
    [
        ("memory_gb", "32", 32.0),
        ("memory_gb", "32 GB", 32.0),
        ("memory_gb", "32768 MB", 32.0),  # MB -> GB
        ("memory_gb", "0.5 TB", 512.0),  # TB -> GB
        ("memory_gb", "16 GiB", 16.0),
        ("memory_gb", "1,024 MB", 1.0),  # thousands separator + MB
        ("disk_gb", "2 TB", 2048.0),
        ("disk_gb", "512000 MB", 500.0),
        ("disk_throughput_mbps", "1 GB/s", 1024.0),
        ("disk_throughput_mbps", "170", 170.0),
        ("cpu_utilization_pct", "58%", 58.0),
        ("cpu_utilization_pct", "0.58", 58.0),  # bare ratio -> percent
        ("cpu_utilization_pct", "58", 58.0),
        ("vcpus", "8 vCPU", 8.0),
        ("vcpus", "8", 8.0),
        ("disk_iops", "3,200", 3200.0),
        ("memory_gb", "N/A", None),
        ("memory_gb", "", None),
        ("memory_gb", None, None),
    ],
)
def test_to_canonical(field, raw, expected):
    result = to_canonical(field, raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=1e-3)


def test_canonical_header_expanded_aliases():
    assert canonical_header("VM Name") == "server"
    assert canonical_header("fqdn") == "server"
    assert canonical_header("Cores") == "vcpus"
    assert canonical_header("RAM (MB)") == "memory_gb"  # 'ram' alias; unit handled at value time
    assert canonical_header("Operating System") == "os"
    assert canonical_header("Datacenter") == "region"


def test_is_plausible_bounds():
    assert is_plausible("memory_gb", 64)
    assert not is_plausible("memory_gb", 32768000)  # 32 PB -> unit misread
    assert not is_plausible("cpu_utilization_pct", 250)
    assert is_plausible("vcpus", 8)
    assert not is_plausible("vcpus", 0)


# --------------------------------------------------------------------------- #
# Conflict comparison collapses equal magnitudes, keeps real differences
# --------------------------------------------------------------------------- #
def test_comparison_value_collapses_equal_units():
    assert comparison_value("memory_gb", "16 GB") == comparison_value("memory_gb", "16384 MB")
    assert comparison_value("memory_gb", "16") == comparison_value("memory_gb", "16.0")
    assert comparison_value("vcpus", "4 vCPU") == comparison_value("vcpus", "4")


def test_comparison_value_keeps_real_conflicts():
    assert comparison_value("memory_gb", "16 GB") != comparison_value("memory_gb", "32 GB")
    # A non-measured field still compares as a trimmed string.
    assert comparison_value("os", "RHEL 9") == comparison_value("os", "rhel 9")
    assert comparison_value("os", "RHEL 9") != comparison_value("os", "Ubuntu 22.04")


# --------------------------------------------------------------------------- #
# End-to-end: heterogeneous units feed sizing correctly
# --------------------------------------------------------------------------- #
def _server(attrs: dict) -> Server:
    return Server(assessment_id="a", name="srv", normalized_key="srv", confidence=0.9, attributes=attrs)


def test_memory_in_mb_is_not_treated_as_gb():
    """The original bug: '65536 MB' parsed as 65536 GB. It must normalize to 64 GB."""
    profile = normalize_workload(_server({"vcpus": "8", "memory_gb": "65536 MB", "os": "Linux"}))
    assert profile["memory_gb"] == pytest.approx(64.0)


def test_disk_in_tb_normalized():
    profile = normalize_workload(_server({"disk_gb": "2 TB", "os": "Linux"}))
    assert profile["disk_gb"] == pytest.approx(2048.0)


def test_source_specific_aliases_are_found():
    """Values stored under source-specific headers (ram, cores, storage) still resolve."""
    profile = normalize_workload(_server({"cores": "8", "ram": "32 GB", "storage": "500 GB", "os": "Linux"}))
    assert profile["vcpus"] == 8.0
    assert profile["memory_gb"] == pytest.approx(32.0)
    assert profile["disk_gb"] == pytest.approx(500.0)


def test_implausible_value_dropped_and_flagged_for_review():
    profile = normalize_workload(_server({"vcpus": "8", "memory_gb": "999999999 GB", "os": "Linux"}))
    assert profile["memory_gb"] is None  # dropped
    assert any("memory_gb" in f for f in profile["implausible_fields"])
    result = size_profile(profile)
    assert result["needs_human_review"] is True
    assert any("implausible" in a.lower() for a in result["assumptions"])


def test_heterogeneous_headers_and_os_column_extracted():
    """A CMDB using 'Host', 'Cores', 'RAM', and an OS column outside the old 5-family
    regex still produces canonical, cited server claims — including the OS."""
    from app.models.entities import Chunk, Document, DocumentType
    from app.services.heuristic_extract import heuristic_extract

    doc = Document(id="d", doc_type=DocumentType.inventory)
    chunk = Chunk(
        id="c",
        assessment_id="a",
        document_id="d",
        chunk_index=0,
        text="Host | Cores | RAM | Operating System\napp-01 | 4 | 16 GB | CentOS 7",
    )
    claims = {(c.attribute, c.value) for c in heuristic_extract([chunk], {"d": doc}).claims}
    assert ("vcpus", "4") in claims
    assert ("memory_gb", "16 GB") in claims  # raw value preserved as evidence
    assert ("os", "CentOS 7") in claims  # OS captured despite not matching the OS regex


def test_mixed_unit_workload_sizes_reasonably():
    """A server described entirely in MB/ratio units sizes as if it were the GB/% equivalent."""
    mb_profile = normalize_workload(
        _server(
            {
                "vcpus": "4",
                "memory_gb": "16384 MB",
                "cpu_utilization_pct": "0.7",
                "disk_gb": "256000 MB",
                "os": "Linux",
                "environment": "prod",
            }
        )
    )
    gb_profile = normalize_workload(
        _server(
            {
                "vcpus": "4",
                "memory_gb": "16 GB",
                "cpu_utilization_pct": "70%",
                "disk_gb": "250 GB",
                "os": "Linux",
                "environment": "prod",
            }
        )
    )
    assert size_profile(mb_profile)["recommended_sku"] == size_profile(gb_profile)["recommended_sku"]
