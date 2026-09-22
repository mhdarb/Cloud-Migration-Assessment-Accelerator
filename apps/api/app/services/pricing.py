from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from app.config import get_settings

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalogs" / "azure_skus.json"


@lru_cache
def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=128)
def live_hourly_price(sku: str, region: str, currency: str) -> float | None:
    settings = get_settings()
    if not settings.azure_pricing_live:
        return None
    filter_value = (
        f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' "
        f"and armSkuName eq '{sku}' and priceType eq 'Consumption'"
    )
    url = (
        "https://prices.azure.com/api/retail/prices"
        f"?currencyCode={quote(currency)}&$filter={quote(filter_value)}"
    )
    try:
        with urlopen(url, timeout=settings.pricing_timeout_seconds) as response:  # noqa: S310
            items = json.load(response).get("Items") or []
        linux = [
            item
            for item in items
            if "windows" not in str(item.get("productName", "")).lower()
            and float(item.get("retailPrice") or 0) > 0
        ]
        return min(float(item["retailPrice"]) for item in linux) if linux else None
    except Exception as exc:
        logger.warning("Azure Retail Prices lookup failed for %s: %s", sku, exc)
        return None


def price_vm(vm: dict, region: str, currency: str) -> dict:
    live = live_hourly_price(vm["name"], region, currency)
    hourly = live if live is not None else float(vm["hourly"])
    return {
        "hourly": round(hourly, 4),
        "monthly": round(hourly * 730, 2),
        "currency": currency,
        "source": "azure-retail-prices" if live is not None else "local-catalog",
        "estimate": True,
    }
