"""Tests for the HTML parsing functions in app.parsers."""

from __future__ import annotations

from app.parsers import (
    clean_text,
    parse_consumption,
    parse_hierarchy,
    parse_meter_details,
    parse_meters,
    to_datetime,
    to_float,
)


# --- Normalisation helpers -----------------------------------------------------


def test_clean_text_strips_and_nones():
    assert clean_text("  hello  ") == "hello"
    assert clean_text("") is None
    assert clean_text("   ") is None
    assert clean_text(None) is None


def test_to_float_handles_units_and_separators():
    assert to_float("124.50") == 124.50
    assert to_float("Rs. 1,234.50") == 1234.50
    assert to_float("  98 ") == 98.0
    assert to_float("not-a-number") is None
    assert to_float("") is None
    assert to_float(None) is None


def test_to_datetime_supports_multiple_formats():
    assert to_datetime("2024-03-01T00:00:00") is not None
    assert to_datetime("2024-03-01") is not None
    assert to_datetime("01/03/2024") is not None
    assert to_datetime("garbage") is None
    assert to_datetime("") is None


# --- Meters parser ------------------------------------------------------------


def test_parse_meters_returns_all_rows(meters_html):
    meters = parse_meters(meters_html)
    assert len(meters) == 4
    assert meters[0] == {
        "meter_id": "MTR-001",
        "serial_number": "SN-100001",
        "status": "ACTIVE",
        "location": "Site A - Building 1",
    }


def test_parse_meters_preserves_fault_status(meters_html):
    meters = parse_meters(meters_html)
    assert meters[2]["meter_id"] == "MTR-003"
    assert meters[2]["status"] == "FAULT"


def test_parse_meters_empty_html_returns_empty():
    assert parse_meters("<html><body><h1>no meters</h1></body></html>") == []


def test_parse_meters_missing_column_defaults_status(meters_html):
    # Header-driven mapping still defaults status to UNKNOWN when absent.
    meters = parse_meters(
        "<table><tr><th>Meter ID</th></tr><tr><td>MTR-X</td></tr></table>"
    )
    assert meters[0]["status"] == "UNKNOWN"
    assert meters[0]["meter_id"] == "MTR-X"


# --- Meter detail parser ------------------------------------------------------


def test_parse_meter_details_extracts_all_fields(meter_detail_html):
    data = parse_meter_details(meter_detail_html, "MTR-001")
    assert data is not None
    assert data["meter_id"] == "MTR-001"
    assert data["serial_number"] == "SN-100001"
    assert data["status"] == "ACTIVE"
    assert data["location"] == "Site A - Building 1"
    assert data["install_date"] is not None
    assert data["last_reading"] == 124.50
    assert data["firmware_version"] == "v2.3.1"
    assert data["customer"] == "Acme Industries"


def test_parse_meter_details_not_found_page_returns_none(meter_not_found_html):
    data = parse_meter_details(meter_not_found_html, "MTR-999")
    assert data is None


def test_parse_meter_details_handles_missing_fields():
    html = """
    <html><body>
      <span id="meter-serial">SN-1</span>
      <span id="meter-status">ACTIVE</span>
    </body></html>
    """
    data = parse_meter_details(html, "MTR-1")
    assert data is not None
    assert data["serial_number"] == "SN-1"
    assert data["status"] == "ACTIVE"
    assert data["location"] is None
    assert data["install_date"] is None


# --- Consumption parser -------------------------------------------------------


def test_parse_consumption_returns_readings(consumption_html):
    data = parse_consumption(consumption_html, "MTR-001")
    assert data["meter_id"] == "MTR-001"
    assert len(data["readings"]) == 5
    first = data["readings"][0]
    assert first["kwh"] == 120.50
    assert first["unit"] == "kWh"
    assert first["timestamp"] is not None


def test_parse_consumption_skips_unparseable_rows():
    html = """
    <table class="consumption-table">
      <tr><th>Timestamp</th><th>kWh</th></tr>
      <tr><td>2024-03-01</td><td>120.50</td></tr>
      <tr><td>garbage</td><td>98.0</td></tr>
      <tr><td>2024-03-03</td><td>not-a-number</td></tr>
    </table>
    """
    data = parse_consumption(html, "MTR-001")
    assert len(data["readings"]) == 1
    assert data["readings"][0]["kwh"] == 120.50


def test_parse_consumption_empty_returns_no_readings():
    data = parse_consumption("<html><body></body></html>", "MTR-001")
    assert data["readings"] == []
    assert data["meter_id"] == "MTR-001"


# --- Hierarchy parser ---------------------------------------------------------


def test_parse_hierarchy_builds_nested_tree(hierarchy_html):
    tree = parse_hierarchy(hierarchy_html)
    assert tree["node_type"] == "root"
    assert len(tree["children"]) == 2
    north = tree["children"][0]
    assert north["name"] == "Region North"
    assert len(north["children"]) == 2
    site_a = north["children"][0]
    assert site_a["name"] == "Site A"
    meters = site_a["children"]
    assert len(meters) == 2
    assert meters[0]["node_type"] == "meter"


def test_parse_hierarchy_empty_returns_root():
    tree = parse_hierarchy("<html><body><h1>no hierarchy</h1></body></html>")
    assert tree["node_type"] == "root"
    assert tree["children"] == []
