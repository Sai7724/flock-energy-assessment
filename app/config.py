"""Application configuration sourced from environment variables.

Settings are read once at import time. Create a ``.env`` file (gitignored) in
the project root to override defaults locally, e.g.::

    PORTAL_BASE_URL=https://urja-ops.flockenergy.tech
    PORTAL_USERNAME=operator@urja.local
    PORTAL_PASSWORD=urja-ops-2026
    REQUEST_TIMEOUT=10.0

The defaults below match the credentials supplied in the assignment brief so
the service runs out of the box without a ``.env`` file for development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env if present (development convenience); ignored in production.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Immutable application settings.

    Attributes:
        portal_base_url: Base URL of the legacy Urja Meter Ops portal.
        portal_username: Default operator username used for auto-reauth.
        portal_password: Default operator password used for auto-reauth.
        request_timeout: HTTP request timeout in seconds.
        login_path: Portal login endpoint path (adjust after recon).
        meters_path: Portal meters listing path (adjust after recon).
        meter_detail_path: Portal meter detail path template (adjust after recon).
        consumption_path: Portal consumption path template (adjust after recon).
        hierarchy_path: Portal hierarchy path (adjust after recon).
        session_cookie_name: Expected session cookie name (adjust after recon).
    """

    portal_base_url: str
    portal_username: str
    portal_password: str
    request_timeout: float

    # --- Portal endpoint paths -------------------------------------------------
    # RECON TODO: verify these paths against the live portal. They are initial
    # assumptions based on the assignment brief; update PROTOCOL.md and here.
    login_path: str = "/login"
    meters_path: str = "/meters"
    meter_detail_path: str = "/meters/{meter_id}"
    consumption_path: str = "/meters/{meter_id}/consumption"
    hierarchy_path: str = "/hierarchy"
    session_cookie_name: str = "session_id"


def get_settings() -> Settings:
    """Build settings from environment variables with sensible defaults.

    Centralised in a function so tests can monkeypatch environment values
    and re-import cleanly.
    """
    return Settings(
        portal_base_url=os.getenv(
            "PORTAL_BASE_URL", "https://urja-ops.flockenergy.tech"
        ).rstrip("/"),
        portal_username=os.getenv("PORTAL_USERNAME", "operator@urja.local"),
        portal_password=os.getenv("PORTAL_PASSWORD", "urja-ops-2026"),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "10.0")),
    )


# Module-level singleton consumed by the API layer.
settings = get_settings()
