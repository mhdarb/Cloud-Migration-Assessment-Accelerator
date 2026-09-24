"""Canonical normalization for measured infrastructure fields.

Heterogeneous inventory sources express the same quantity in different units and
headers: memory in MB/GB/TB (or MiB/GiB), disk in GB/TB, CPU as "cores"/"vCPU",
utilization with or without a "%". Extraction keeps the *raw* string as evidence;
this module maps a source header to a canonical field and converts a raw value to a
single canonical unit per field, so the sizing engine and conflict detection are
unit-correct rather than comparing display strings.

Canonical units: memory_gb / disk_gb in GB, disk_throughput_mbps in MB/s,
*_utilization_pct in percent, vcpus in cores, disk_iops in IOPS.

This is the single source of truth for the column-alias vocabulary — `heuristic_extract`
and `sizing` both import from here so the mapping can't drift between extraction and
sizing.
"""

from __future__ import annotations

import re

# Canonical field -> the set of header spellings (already snake-normalized) that map to it.
INFRA_COLUMN_ALIASES: dict[str, set[str]] = {
    "server": {
        "server",
        "hostname",
        "host",
        "host_name",
        "server_name",
        "vm",
        "vm_name",
        "instance",
        "instance_id",
        "instance_name",
        "machine",
        "machine_name",
        "computer_name",
        "node",
        "node_name",
        "device",
        "device_name",
        "fqdn",
        "name",
    },
    "vcpus": {"vcpus", "vcpu", "cpu_cores", "cores", "cpu", "cpu_count", "processors", "num_cpus"},
    "memory_gb": {"memory_gb", "memory", "ram_gb", "ram", "mem", "mem_gb", "memory_mb", "ram_mb"},
    "cpu_utilization_pct": {
        "cpu_utilization_pct",
        "cpu_utilization",
        "avg_cpu_pct",
        "cpu_pct",
        "cpu_usage",
        "cpu_usage_pct",
    },
    "memory_utilization_pct": {
        "memory_utilization_pct",
        "memory_utilization",
        "avg_memory_pct",
        "memory_pct",
        "mem_utilization",
        "memory_usage_pct",
    },
    "disk_gb": {
        "disk_gb",
        "storage_gb",
        "disk_capacity_gb",
        "storage",
        "disk",
        "disk_size",
        "disk_size_gb",
        "capacity_gb",
    },
    "disk_iops": {"disk_iops", "iops", "provisioned_iops"},
    "disk_throughput_mbps": {"disk_throughput_mbps", "throughput_mbps", "disk_throughput", "throughput"},
    "os": {"os", "operating_system", "os_name", "platform", "os_version"},
    "environment": {"environment", "env", "stage", "tier_env"},
    "region": {"region", "location", "datacenter", "data_center", "az", "availability_zone"},
    "architecture": {"architecture", "arch", "cpu_architecture", "isa"},
}

# The subset that carries a numeric measurement fed into the sizing math.
MEASURED_FIELDS: tuple[str, ...] = (
    "vcpus",
    "memory_gb",
    "cpu_utilization_pct",
    "memory_utilization_pct",
    "disk_gb",
    "disk_iops",
    "disk_throughput_mbps",
)

# Multipliers to convert a capacity value to GB. Binary base (1024): the ~2.4% decimal
# vs binary difference is well below the catalog's SKU granularity, and memory — the most
# size-sensitive field — is conventionally binary. Unknown/blank unit is assumed GB.
_CAPACITY_TO_GB: dict[str, float] = {
    "": 1.0,
    "b": 1.0 / (1024**3),
    "kb": 1.0 / (1024**2),
    "kib": 1.0 / (1024**2),
    "mb": 1.0 / 1024,
    "mib": 1.0 / 1024,
    "gb": 1.0,
    "gib": 1.0,
    "tb": 1024.0,
    "tib": 1024.0,
    "pb": 1024.0 * 1024.0,
    "pib": 1024.0 * 1024.0,
}

# Plausibility bounds per canonical field; a value outside these is almost certainly a
# unit misread or a typo and should not be trusted by the sizing math unchecked.
MEASURE_BOUNDS: dict[str, tuple[float, float]] = {
    "vcpus": (1, 1024),
    "memory_gb": (0.5, 24576),  # up to 24 TB
    "cpu_utilization_pct": (0, 100),
    "memory_utilization_pct": (0, 100),
    "disk_gb": (1, 1_048_576),  # up to 1 PB
    "disk_iops": (1, 20_000_000),
    "disk_throughput_mbps": (1, 100_000),
}

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_UNIT = re.compile(r"[a-zµ/%]+")


def canonical_header(value: str) -> str:
    """Map a raw column header to its canonical field name (or a snake-cased passthrough)."""
    key = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    for canonical, aliases in INFRA_COLUMN_ALIASES.items():
        if key in aliases:
            return canonical
    return key


def _capacity_gb(number: float, unit: str) -> float:
    token = _UNIT.match(unit)
    key = token.group() if token else ""
    return number * _CAPACITY_TO_GB.get(key, 1.0)


def to_canonical(field: str, raw: object) -> float | None:
    """Convert a raw measured value to its canonical unit for `field`, honoring an
    embedded unit suffix (e.g. "16384 MB" -> 16.0 GB, "0.5 TB" -> 512.0 GB, "58%" -> 58,
    "8 vCPU" -> 8). Returns None when there's no parseable number."""
    if raw is None:
        return None
    text = str(raw).strip().lower().replace(",", "")
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    number = float(match.group())
    unit = text[match.end() :].strip()

    if field in ("memory_gb", "disk_gb"):
        return _capacity_gb(number, unit)
    if field == "disk_throughput_mbps":
        if unit.startswith(("gb", "gib", "g/")):  # GB/s -> MB/s
            return number * 1024.0
        return number
    if field.endswith("_pct"):
        # A bare fraction in [0,1] with no "%" is a ratio (0.58 -> 58%); anything else is
        # already a percentage figure.
        if "%" not in text and 0 < number < 1:
            return number * 100.0
        return number
    # vcpus, disk_iops, and any other numeric field: the count itself.
    return number


def is_plausible(field: str, value: float | None) -> bool:
    """True if `value` is within the sane range for `field` (or the field is unbounded)."""
    if value is None:
        return True
    low, high = MEASURE_BOUNDS.get(field, (float("-inf"), float("inf")))
    return low <= value <= high


def comparison_value(attribute: str, raw: object) -> str:
    """The value used to decide whether two claims for the same attribute actually
    conflict. For a measured field this is the canonical number (so "16 GB", "16",
    "16.0" and "16384 MB" all collapse to one value and don't raise a spurious conflict,
    while "16 GB" vs "32 GB" still does). For everything else it's the trimmed raw string."""
    if attribute in MEASURED_FIELDS:
        number = to_canonical(attribute, raw)
        if number is not None:
            return format(round(number, 3), "g")
    return str(raw).strip().lower()
