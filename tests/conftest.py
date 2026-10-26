"""Shared pytest fixtures for the Urja Meter Ops API test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Read a fixture file from tests/fixtures/ as text."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def meters_html() -> str:
    return load_fixture("meters_list.html")


@pytest.fixture
def meter_detail_html() -> str:
    return load_fixture("meter_detail.html")


@pytest.fixture
def consumption_html() -> str:
    return load_fixture("consumption.html")


@pytest.fixture
def hierarchy_html() -> str:
    return load_fixture("hierarchy.html")


@pytest.fixture
def meter_not_found_html() -> str:
    return load_fixture("meter_not_found.html")


@pytest.fixture
def test_settings():
    """Settings pointed at a dummy portal URL for client tests."""
    from app.config import Settings

    return Settings(
        portal_base_url="https://portal.test",
        portal_username="testuser",
        portal_password="testpass",
        request_timeout=5.0,
    )
