"""Optional API authentication for Landing Zone demos.

Production AI LZ: enforce JWT at Azure API Management (hub) with Entra ID.
This middleware is a spoke-side break-glass / local-lz gate when API_AUTH_KEY is set.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.config import get_settings

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


async def require_api_auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    expected = (settings.api_auth_key or "").strip()
    if not expected:
        return
    if request.url.path in PUBLIC_PATHS:
        return

    presented = (x_api_key or "").strip()
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()

    if presented != expected:
        raise HTTPException(
            status_code=401,
            detail=(
                "Unauthorized. Provide X-API-Key (or Bearer). "
                "In Azure AI Landing Zones, prefer Entra JWT validation at APIM."
            ),
        )
