"""Tests for the UrjaPortalClient (login, auto-reauth, data access).

Uses ``httpx.MockTransport`` to simulate portal responses without network
access. The mock handler inspects request URLs and returns canned responses,
including cookies to exercise session persistence.
"""

from __future__ import annotations

import httpx2 as httpx
import pytest

from app.client import (
    AuthenticationError,
    MeterNotFoundError,
    PortalError,
    UrjaPortalClient,
)

from tests.conftest import load_fixture

METERS_HTML = load_fixture("meters_list.html")
METER_DETAIL_HTML = load_fixture("meter_detail.html")
CONSUMPTION_HTML = load_fixture("consumption.html")
HIERARCHY_HTML = load_fixture("hierarchy.html")
NOT_FOUND_HTML = load_fixture("meter_not_found.html")


def _make_client(handler, settings) -> UrjaPortalClient:
    transport = httpx.MockTransport(handler)
    return UrjaPortalClient(settings, transport=transport)


# --- Login --------------------------------------------------------------------


def test_login_success_sets_authenticated(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login"
        # Simulate a session cookie being set.
        resp = httpx.Response(200, text="ok")
        resp.headers["set-cookie"] = "session_id=abc123; Path=/"
        return resp

    client = _make_client(handler, test_settings)
    assert client.login() is True
    assert client.is_authenticated is True
    # Cookie persisted on the client.
    assert client._client.cookies.get("session_id") == "abc123"


def test_login_rejected_returns_false(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad credentials")

    client = _make_client(handler, test_settings)
    assert client.login("wrong", "creds") is False
    assert client.is_authenticated is False


def test_login_network_error_raises_portal_error(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler, test_settings)
    with pytest.raises(PortalError):
        client.login()


def test_login_unexpected_status_raises_portal_error(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = _make_client(handler, test_settings)
    with pytest.raises(PortalError):
        client.login()


# --- Authenticated GET with auto-reauth ---------------------------------------


def test_get_meters_after_session_ok(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meters":
            return httpx.Response(200, text=METERS_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    client._authenticated = True
    meters = client.get_meters()
    assert len(meters) == 4
    assert meters[0]["meter_id"] == "MTR-001"


def test_auto_reauth_on_401_then_retry_succeeds(test_settings):
    """A 401 on the first GET triggers re-login and the retry returns data."""
    calls = {"count": 0, "login_count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            calls["login_count"] += 1
            resp = httpx.Response(200, text="ok")
            resp.headers["set-cookie"] = "session_id=fresh; Path=/"
            return resp
        if request.url.path == "/meters":
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(401, text="expired")
            return httpx.Response(200, text=METERS_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    meters = client.get_meters()
    assert calls["login_count"] == 1
    assert calls["count"] == 2
    assert len(meters) == 4


def test_persistent_401_raises_authentication_error(test_settings):
    """If re-auth also yields a 401 on retry, raise AuthenticationError."""
    login_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            login_count["n"] += 1
            resp = httpx.Response(200, text="ok")
            resp.headers["set-cookie"] = "session_id=fresh; Path=/"
            return resp
        # Always 401 for /meters, even after re-auth.
        return httpx.Response(401, text="expired")

    client = _make_client(handler, test_settings)
    with pytest.raises(AuthenticationError):
        client.get_meters()
    # Re-login attempted exactly once (no infinite loop).
    assert login_count["n"] == 1


def test_reauth_rejected_credentials_raises_authentication_error(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(401, text="bad creds")
        return httpx.Response(401, text="expired")

    client = _make_client(handler, test_settings)
    with pytest.raises(AuthenticationError):
        client.get_meters()


def test_redirect_to_login_triggers_reauth(test_settings):
    """A 302 to /login while fetching a protected page triggers re-auth."""
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            resp = httpx.Response(200, text="ok")
            resp.headers["set-cookie"] = "session_id=fresh; Path=/"
            return resp
        if request.url.path == "/meters":
            if state["first"]:
                state["first"] = False
                return httpx.Response(302, headers={"location": "/login"})
            return httpx.Response(200, text=METERS_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    client._authenticated = True  # simulate an existing (now expired) session
    meters = client.get_meters()
    assert len(meters) == 4


# --- Data accessors -----------------------------------------------------------


def test_get_meter_details_returns_parsed_dict(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meters/MTR-001":
            return httpx.Response(200, text=METER_DETAIL_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    client._authenticated = True
    data = client.get_meter_details("MTR-001")
    assert data["meter_id"] == "MTR-001"
    assert data["serial_number"] == "SN-100001"
    assert data["last_reading"] == 124.50


def test_get_meter_details_not_found_raises(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meters/MTR-999":
            return httpx.Response(200, text=NOT_FOUND_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    client._authenticated = True
    with pytest.raises(MeterNotFoundError):
        client.get_meter_details("MTR-999")


def test_get_consumption_returns_history(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/meters/MTR-001/consumption":
            return httpx.Response(200, text=CONSUMPTION_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    client._authenticated = True
    data = client.get_consumption("MTR-001")
    assert data["meter_id"] == "MTR-001"
    assert len(data["readings"]) == 5
    assert data["readings"][0]["kwh"] == 120.50


def test_get_hierarchy_returns_tree(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/hierarchy":
            return httpx.Response(200, text=HIERARCHY_HTML)
        return httpx.Response(404)

    client = _make_client(handler, test_settings)
    client._authenticated = True
    tree = client.get_hierarchy()
    assert tree["node_type"] == "root"
    assert len(tree["children"]) == 2


def test_get_meters_5xx_raises_portal_error(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _make_client(handler, test_settings)
    client._authenticated = True
    with pytest.raises(PortalError):
        client.get_meters()
