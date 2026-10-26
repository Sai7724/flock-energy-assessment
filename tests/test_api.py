"""Tests for the FastAPI routes in app.main.

Uses FastAPI's ``TestClient`` with a fake portal client injected via
``app.dependency_overrides`` so the API layer is tested in isolation from
the live portal.
"""

from __future__ import annotations

from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from app.client import (
    AuthenticationError,
    MeterNotFoundError,
    PortalError,
)
from app.main import app, get_portal_client
from app.models import Meter, MeterDetail, ConsumptionHistory, ConsumptionReading, HierarchyNode


class FakePortalClient:
    """Stand-in for UrjaPortalClient with controllable behaviour."""

    def __init__(self) -> None:
        self.login_result: bool = True
        self.meters: List[dict] = []
        self.meter_detail: Optional[dict] = None
        self.consumption: Optional[dict] = None
        self.hierarchy: dict = {"node_id": "root", "name": "Network", "node_type": "root", "children": []}
        # Per-meter overrides for not-found simulation.
        self.raise_meter_not_found = False
        self.raise_portal_error = False
        self.raise_auth_error = False
        self.is_authenticated = True

    def login(self, username: str = "", password: str = "") -> bool:
        return self.login_result

    def get_meters(self):
        if self.raise_portal_error:
            raise PortalError("portal down")
        if self.raise_auth_error:
            raise AuthenticationError("auth failed")
        return self.meters

    def get_meter_details(self, meter_id: str):
        if self.raise_meter_not_found:
            raise MeterNotFoundError(f"Meter '{meter_id}' not found")
        if self.raise_portal_error:
            raise PortalError("portal down")
        return self.meter_detail

    def get_consumption(self, meter_id: str):
        if self.raise_meter_not_found:
            raise MeterNotFoundError(f"Meter '{meter_id}' not found")
        if self.raise_portal_error:
            raise PortalError("portal down")
        return self.consumption

    def get_hierarchy(self):
        if self.raise_portal_error:
            raise PortalError("portal down")
        return self.hierarchy


@pytest.fixture
def fake_client():
    fc = FakePortalClient()
    fc.meters = [
        {"meter_id": "MTR-001", "serial_number": "SN-1", "status": "ACTIVE", "location": "Site A"},
        {"meter_id": "MTR-002", "serial_number": "SN-2", "status": "FAULT", "location": "Site B"},
    ]
    fc.meter_detail = {
        "meter_id": "MTR-001",
        "serial_number": "SN-1",
        "status": "ACTIVE",
        "location": "Site A",
        "last_reading": 124.5,
    }
    fc.consumption = {
        "meter_id": "MTR-001",
        "readings": [
            {"timestamp": "2024-03-01T00:00:00", "kwh": 120.5, "unit": "kWh"},
        ],
    }
    fc.hierarchy = {
        "node_id": "root",
        "name": "Network",
        "node_type": "root",
        "children": [
            {"node_id": "north", "name": "Region North", "node_type": "group", "children": []},
        ],
    }
    app.dependency_overrides[get_portal_client] = lambda: fc
    yield fc
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- Health -------------------------------------------------------------------


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- Auth ---------------------------------------------------------------------


def test_login_success(client, fake_client):
    resp = client.post("/api/v1/auth/login", json={"username": "u", "password": "p"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["session_active"] is True


def test_login_bad_credentials(client, fake_client):
    fake_client.login_result = False
    resp = client.post("/api/v1/auth/login", json={"username": "u", "password": "p"})
    assert resp.status_code == 401
    assert "Invalid credentials" in resp.json()["detail"]


def test_login_missing_fields_returns_422(client, fake_client):
    resp = client.post("/api/v1/auth/login", json={"username": "u"})
    assert resp.status_code == 422


def test_login_portal_error_returns_502(client, fake_client):
    def raise_portal(u="", p=""):
        raise PortalError("portal down")
    fake_client.login = raise_portal  # type: ignore[method-assign]
    resp = client.post("/api/v1/auth/login", json={"username": "u", "password": "p"})
    assert resp.status_code == 502


# --- Meters -------------------------------------------------------------------


def test_list_meters(client, fake_client):
    resp = client.get("/api/v1/meters")
    assert resp.status_code == 200
    meters = resp.json()
    assert len(meters) == 2
    assert meters[0]["meter_id"] == "MTR-001"
    assert meters[1]["status"] == "FAULT"


def test_list_meters_portal_error_returns_502(client, fake_client):
    fake_client.raise_portal_error = True
    resp = client.get("/api/v1/meters")
    assert resp.status_code == 502


def test_list_meters_auth_error_returns_401(client, fake_client):
    fake_client.raise_auth_error = True
    resp = client.get("/api/v1/meters")
    assert resp.status_code == 401


# --- Meter details ------------------------------------------------------------


def test_get_meter_details(client, fake_client):
    resp = client.get("/api/v1/meters/MTR-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meter_id"] == "MTR-001"
    assert body["serial_number"] == "SN-1"
    assert body["last_reading"] == 124.5


def test_get_meter_details_not_found(client, fake_client):
    fake_client.raise_meter_not_found = True
    resp = client.get("/api/v1/meters/MTR-999")
    assert resp.status_code == 404


def test_get_meter_details_portal_error(client, fake_client):
    fake_client.raise_portal_error = True
    resp = client.get("/api/v1/meters/MTR-001")
    assert resp.status_code == 502


# --- Consumption --------------------------------------------------------------


def test_get_consumption(client, fake_client):
    resp = client.get("/api/v1/meters/MTR-001/consumption")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meter_id"] == "MTR-001"
    assert len(body["readings"]) == 1
    assert body["readings"][0]["kwh"] == 120.5


def test_get_consumption_not_found(client, fake_client):
    fake_client.raise_meter_not_found = True
    resp = client.get("/api/v1/meters/MTR-999/consumption")
    assert resp.status_code == 404


# --- Hierarchy ----------------------------------------------------------------


def test_get_hierarchy(client, fake_client):
    resp = client.get("/api/v1/hierarchy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["node_type"] == "root"
    assert len(body["children"]) == 1


def test_get_hierarchy_portal_error(client, fake_client):
    fake_client.raise_portal_error = True
    resp = client.get("/api/v1/hierarchy")
    assert resp.status_code == 502


# --- OpenAPI / docs ------------------------------------------------------------


def test_openapi_schema_served(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "Flock Energy - Urja Meter Ops API"
    paths = schema["paths"]
    for expected in [
        "/api/v1/auth/login",
        "/api/v1/meters",
        "/api/v1/meters/{meter_id}",
        "/api/v1/meters/{meter_id}/consumption",
        "/api/v1/hierarchy",
        "/health",
    ]:
        assert expected in paths, f"missing endpoint in OpenAPI: {expected}"


def test_docs_page_served(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower() or "api" in resp.text.lower()
