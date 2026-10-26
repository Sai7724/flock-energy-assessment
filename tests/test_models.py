"""Tests for Pydantic data models (validation + serialisation)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    ConsumptionHistory,
    ConsumptionReading,
    HierarchyNode,
    LoginRequest,
    LoginResponse,
    Meter,
    MeterDetail,
)


def test_meter_defaults_status_unknown():
    m = Meter(meter_id="MTR-001")
    assert m.meter_id == "MTR-001"
    assert m.status == "UNKNOWN"
    assert m.serial_number is None
    assert m.location is None


def test_meter_detail_inherits_meter_fields():
    detail = MeterDetail(
        meter_id="MTR-001",
        serial_number="SN-1",
        status="ACTIVE",
        location="Site A",
        last_reading=124.5,
    )
    assert detail.serial_number == "SN-1"
    assert detail.last_reading == 124.5
    assert detail.firmware_version is None


def test_login_request_requires_non_empty_fields():
    with pytest.raises(ValidationError):
        LoginRequest(username="", password="x")
    with pytest.raises(ValidationError):
        LoginRequest(username="x", password="")
    with pytest.raises(ValidationError):
        LoginRequest()  # type: ignore[call-arg]


def test_consumption_reading_coerces_strings():
    reading = ConsumptionReading(timestamp="2024-03-01T00:00:00", kwh="120.50", unit="kWh")
    assert reading.kwh == 120.50
    assert reading.unit == "kWh"


def test_consumption_reading_rejects_non_numeric_kwh():
    with pytest.raises(ValidationError):
        ConsumptionReading(timestamp="2024-03-01T00:00:00", kwh="not-a-number")


def test_consumption_history_defaults_to_empty_readings():
    hist = ConsumptionHistory(meter_id="MTR-001")
    assert hist.readings == []


def test_hierarchy_node_recursive_children():
    leaf = HierarchyNode(node_id="MTR-001", name="MTR-001", node_type="meter")
    parent = HierarchyNode(
        node_id="site-a", name="Site A", node_type="group", children=[leaf]
    )
    root = HierarchyNode(
        node_id="root", name="Network", node_type="root", children=[parent]
    )
    assert root.children[0].children[0].node_id == "MTR-001"


def test_login_response_serialisation():
    resp = LoginResponse(success=True, message="ok", session_active=True)
    assert resp.model_dump() == {"success": True, "message": "ok", "session_active": True}
