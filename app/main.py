"""FastAPI application exposing the Urja Meter Ops REST API.

Routes
------
- ``GET  /health``                       Liveness probe.
- ``POST /api/v1/auth/login``            Explicit portal login.
- ``GET  /api/v1/meters``                List smart meters.
- ``GET  /api/v1/meters/{meter_id}``     Meter details.
- ``GET  /api/v1/meters/{meter_id}/consumption``  Consumption history.
- ``GET  /api/v1/hierarchy``             Network hierarchy tree (optional).

FastAPI auto-serves the OpenAPI schema at ``/openapi.json`` and interactive
Swagger UI at ``/docs``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import Depends, FastAPI, HTTPException, status

from .client import (
    AuthenticationError,
    MeterNotFoundError,
    PortalError,
    UrjaPortalClient,
)
from .config import settings
from .models import (
    ConsumptionHistory,
    ErrorDetail,
    HierarchyNode,
    LoginRequest,
    LoginResponse,
    Meter,
    MeterDetail,
)

logger = logging.getLogger(__name__)


# --- Shared client instance --------------------------------------------------
# A single shared client persists the portal session. This is an explicit
# trade-off (see README.md): simpler and correct for a single-process service,
# but it serialises portal access. Production should inject per-caller clients.

_portal_client = UrjaPortalClient(settings)


def get_portal_client() -> UrjaPortalClient:
    """FastAPI dependency returning the shared portal client.

    Tests can override this with ``app.dependency_overrides[get_portal_client]``.
    """
    return _portal_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: log startup/shutdown and clean up the client."""
    logger.info("Starting Urja Meter Ops API (portal=%s)", settings.portal_base_url)
    yield
    logger.info("Shutting down; closing portal client")
    _portal_client.close()


app = FastAPI(
    title="Flock Energy - Urja Meter Ops API",
    version="1.0.0",
    description=(
        "A clean REST API proxy layer over the legacy Urja Meter Ops portal. "
        "Automates authentication and session management, fetches smart meter "
        "data, and normalises HTML responses into structured JSON."
    ),
    lifespan=lifespan,
)


# --- Error mapping helper ------------------------------------------------------


def _map_client_error(exc: Exception) -> HTTPException:
    """Translate client-layer exceptions into HTTP responses."""
    if isinstance(exc, MeterNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    if isinstance(exc, AuthenticationError):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        )
    if isinstance(exc, PortalError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unexpected error: {exc}",
    )


# --- Routes --------------------------------------------------------------------


@app.get("/health", tags=["health"], summary="Liveness probe")
def health() -> dict:
    """Return service health. Does not touch the portal."""
    return {"status": "ok"}


@app.post(
    "/api/v1/auth/login",
    tags=["auth"],
    response_model=LoginResponse,
    summary="Authenticate against the Urja portal",
    responses={
        401: {"model": ErrorDetail, "description": "Invalid credentials"},
        502: {"model": ErrorDetail, "description": "Portal unreachable"},
    },
)
def login(
    body: LoginRequest,
    client: UrjaPortalClient = Depends(get_portal_client),
) -> LoginResponse:
    """Explicitly log in to the portal using provided credentials.

    The shared session is established for subsequent data requests. Auto-
    reauthentication still kicks in if the session later expires.
    """
    try:
        ok = client.login(body.username, body.password)
    except PortalError as exc:
        raise _map_client_error(exc) from exc
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return LoginResponse(
        success=True,
        message="Authentication successful",
        session_active=client.is_authenticated,
    )


@app.get(
    "/api/v1/meters",
    tags=["meters"],
    response_model=List[Meter],
    summary="List smart meters",
    responses={
        401: {"model": ErrorDetail, "description": "Authentication failed"},
        502: {"model": ErrorDetail, "description": "Portal error"},
    },
)
def list_meters(
    client: UrjaPortalClient = Depends(get_portal_client),
) -> List[Meter]:
    """Retrieve the list of smart meters from the portal."""
    try:
        meters = client.get_meters()
    except PortalError as exc:
        raise _map_client_error(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise _map_client_error(exc) from exc
    return meters


@app.get(
    "/api/v1/meters/{meter_id}",
    tags=["meters"],
    response_model=MeterDetail,
    summary="Get meter details",
    responses={
        404: {"model": ErrorDetail, "description": "Meter not found"},
        401: {"model": ErrorDetail, "description": "Authentication failed"},
        502: {"model": ErrorDetail, "description": "Portal error"},
    },
)
def get_meter(
    meter_id: str,
    client: UrjaPortalClient = Depends(get_portal_client),
) -> MeterDetail:
    """Retrieve details for a specific meter."""
    try:
        data = client.get_meter_details(meter_id)
    except PortalError as exc:
        raise _map_client_error(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise _map_client_error(exc) from exc
    return data


@app.get(
    "/api/v1/meters/{meter_id}/consumption",
    tags=["meters"],
    response_model=ConsumptionHistory,
    summary="Get consumption history",
    responses={
        404: {"model": ErrorDetail, "description": "Meter not found"},
        401: {"model": ErrorDetail, "description": "Authentication failed"},
        502: {"model": ErrorDetail, "description": "Portal error"},
    },
)
def get_consumption(
    meter_id: str,
    client: UrjaPortalClient = Depends(get_portal_client),
) -> ConsumptionHistory:
    """Retrieve consumption history for a specific meter."""
    try:
        data = client.get_consumption(meter_id)
    except PortalError as exc:
        raise _map_client_error(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise _map_client_error(exc) from exc
    return data


@app.get(
    "/api/v1/hierarchy",
    tags=["hierarchy"],
    response_model=HierarchyNode,
    summary="Get meter network hierarchy (optional extension)",
    responses={
        401: {"model": ErrorDetail, "description": "Authentication failed"},
        502: {"model": ErrorDetail, "description": "Portal error"},
    },
)
def get_hierarchy(
    client: UrjaPortalClient = Depends(get_portal_client),
) -> HierarchyNode:
    """Retrieve the organised meter network hierarchy tree.

    Optional extension endpoint. If the portal does not expose hierarchy data,
    a single root node with no children is returned.
    """
    try:
        data = client.get_hierarchy()
    except PortalError as exc:
        raise _map_client_error(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise _map_client_error(exc) from exc
    return data
