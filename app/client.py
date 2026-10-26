"""Legacy Urja Meter Ops portal HTTP adapter.

The :class:`UrjaPortalClient` owns a persistent ``httpx.Client`` (so cookies
survive across requests), handles login, transparently re-authenticates when
a session expires, and delegates HTML parsing to :mod:`app.parsers`.

Architecture note
-----------------
A single shared client instance is used by the API layer (see ``main.py``).
This is acceptable for a single-process deployment but is an explicit
trade-off: it serialises portal access through one session. A production
deployment should use dependency injection or a session pool keyed per
caller. See README.md "Architectural trade-offs".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx2 as httpx

from .config import Settings
from . import parsers

logger = logging.getLogger(__name__)


# --- Exceptions ---------------------------------------------------------------


class PortalError(Exception):
    """Generic failure talking to the legacy portal (network, unexpected HTTP)."""


class AuthenticationError(PortalError):
    """Login failed or the session could not be established."""


class MeterNotFoundError(PortalError):
    """A requested meter does not exist on the portal."""


# --- Client -------------------------------------------------------------------


class UrjaPortalClient:
    """Adapter that talks to the Urja Meter Ops portal.

    Args:
        settings: Application :class:`~app.config.Settings`.
    """

    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None) -> None:
        """Initialise the portal client.

        Args:
            settings: Application :class:`~app.config.Settings`.
            transport: Optional ``httpx`` transport. Tests inject a
                ``httpx.MockTransport``; production leaves this as ``None`` to
                use the default network transport.
        """
        self.settings = settings
        # Persistent client retains cookies across requests for the session.
        self._client = httpx.Client(timeout=settings.request_timeout, transport=transport)
        self._authenticated = False
        # Tracks whether the current call frame is already a re-auth attempt,
        # preventing infinite re-login loops.
        self._reauth_in_progress = False

    # --- Session lifecycle ----------------------------------------------------

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """Authenticate against the portal and establish a session.

        The portal is a SvelteKit app whose login form POSTs ``email`` and
        ``password`` as form-encoded data to ``/login``. SvelteKit enforces a
        CSRF check on the ``Origin`` header, so we send an ``Origin`` matching
        the portal's own origin. On success the form action returns a 303
        redirect away from ``/login``; on failure it re-renders ``/login`` (200).

        The ``username`` argument maps to the portal's ``email`` field so our
        public API can keep a generic ``username`` contract.

        Returns ``True`` on success, ``False`` on rejected credentials.
        Raises :class:`PortalError` on transport failures, 5xx responses, or a
        403 (CSRF/origin check failure).
        """
        email = username or self.settings.portal_username
        password = password or self.settings.portal_password
        login_url = self.settings.portal_base_url + self.settings.login_path

        payload = {"email": email, "password": password}
        headers = {
            "Origin": self.settings.portal_base_url,
            "Referer": login_url,
        }

        try:
            response = self._client.post(
                login_url,
                data=payload,
                follow_redirects=True,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise PortalError(f"Failed to reach portal login: {exc}") from exc

        if response.status_code >= 500:
            raise PortalError(
                f"Portal server error {response.status_code} during login"
            )
        if response.status_code == 403:
            raise PortalError(
                "Login rejected with 403 (CSRF/origin check failed)"
            )
        if response.status_code == 401:
            self._authenticated = False
            logger.warning("Portal login rejected for user '%s' (HTTP 401)", email)
            return False

        # SvelteKit JSON form action response inspection:
        # Success: {"type":"redirect","status":303,"location":"/meters"}
        # Failure: {"type":"failure","status":401,...}
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = response.json()
                if body.get("type") == "failure" or body.get("status") in (401, 403):
                    self._authenticated = False
                    logger.warning("Portal login rejected for user '%s' (JSON failure payload)", email)
                    return False
                if body.get("type") == "redirect":
                    self._authenticated = True
                    logger.info("Portal login successful for user '%s' (JSON redirect to %s)", email, body.get("location"))
                    return True
            except Exception:
                pass

        # Traditional HTTP redirect check:
        final_path = (response.url.path or "/").rstrip("/") or "/"
        login_path = self.settings.login_path.rstrip("/")
        if final_path != login_path:
            self._authenticated = True
            logger.info("Portal login successful for user '%s' (redirected to %s)", email, final_path)
            return True

        # Session cookie check (e.g. Set-Cookie returned with HTTP 200):
        has_session_cookie = bool(
            self._client.cookies.get(self.settings.session_cookie_name)
            or any("session" in k.lower() or "auth" in k.lower() for k in self._client.cookies.keys())
            or "set-cookie" in response.headers
        )
        if response.status_code == 200 and has_session_cookie:
            self._authenticated = True
            logger.info("Portal login successful for user '%s' (session cookie established)", email)
            return True

        self._authenticated = False
        logger.warning("Portal login rejected for user '%s' (still on login page)", email)
        return False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        self._client.close()

    # --- Authenticated GET with auto-reauth -----------------------------------

    def _looks_like_auth_failure(self, response: httpx.Response) -> bool:
        """Detect an expired session (401 or a redirect to the login page).

        ``_get`` follows redirects, so a 302 to ``/login`` results in a 200
        response whose final URL is the login page. We detect that by
        comparing the final response URL path to the configured login path.
        """
        if response.status_code == 401:
            return True
        final_path = (response.url.path or "").rstrip("/")
        login_path = self.settings.login_path.rstrip("/")
        # Landing on the login page while fetching a protected resource means
        # the session expired. We exclude the empty-path edge case.
        if final_path and final_path == login_path:
            return True
        return False

    def _get(self, path: str) -> str:
        """Perform an authenticated GET, auto-reauthenticating once on expiry.

        Returns the response body text. Raises :class:`AuthenticationError`
        if re-authentication fails, :class:`PortalError` for other failures.
        """
        url = self.settings.portal_base_url + path
        try:
            response = self._client.get(url, follow_redirects=True)
        except httpx.RequestError as exc:
            raise PortalError(f"Failed to reach portal at {path}: {exc}") from exc

        if self._looks_like_auth_failure(response) and not self._reauth_in_progress:
            logger.info("Session expired while fetching %s; re-authenticating", path)
            self._reauth_in_progress = True
            try:
                ok = self.login()
            finally:
                self._reauth_in_progress = False
            if not ok:
                raise AuthenticationError("Re-authentication failed (rejected credentials)")
            # Retry the original request once after re-auth.
            try:
                response = self._client.get(url, follow_redirects=True)
            except httpx.RequestError as exc:
                raise PortalError(f"Retry failed for {path}: {exc}") from exc
            if self._looks_like_auth_failure(response):
                raise AuthenticationError("Session still invalid after re-authentication")

        if response.status_code == 404:
            raise PortalError(f"Portal returned 404 for {path}")
        if response.status_code >= 500:
            raise PortalError(f"Portal server error {response.status_code} for {path}")
        if response.status_code >= 400:
            raise PortalError(f"Portal returned HTTP {response.status_code} for {path}")

        return response.text

    # --- Signed export helper -------------------------------------------------

    def _get_export_data(self) -> Optional[list]:
        """Fetch all meters using the HMAC-signed /portal/export endpoint if available."""
        try:
            keys_raw = self._get("/portal/keys")
            secret = json.loads(keys_raw).get("data", {}).get("signingSecret")
            if not secret:
                return None
            ts = str(int(time.time()))
            query = "page=1"
            path = "/portal/export"
            msg = f"GET\n{path}\n{query}\n{ts}".encode("utf-8")
            sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
            headers = {"x-timestamp": ts, "x-signature": sig}
            url = f"{self.settings.portal_base_url}{path}?{query}"
            resp = self._client.get(url, headers=headers, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
        except Exception as exc:
            logger.debug("Signed export endpoint unavailable: %s", exc)
        return None

    # --- Data accessors (delegate parsing to app.parsers with live fallbacks) --

    def get_meters(self) -> list:
        """Retrieve the list of smart meters.

        First attempts to parse the HTML listing at /meters. If the table is empty
        (as on the live SvelteKit portal which hydrates meters asynchronously),
        it attempts the signed export endpoint or the internal /portal/meters/search API.
        """
        meters = []
        try:
            html = self._get(self.settings.meters_path)
            meters = parsers.parse_meters(html)
            if meters:
                return meters
        except PortalError as exc:
            if "404" not in str(exc):
                raise

        # Live portal fallback: try signed export first (returns all 403 meters in 1 request)
        export_items = self._get_export_data()
        if export_items:
            results = []
            for item in export_items:
                results.append(
                    {
                        "meter_id": item.get("meterId"),
                        "serial_number": item.get("serialNo"),
                        "status": item.get("installStatus") or "UNKNOWN",
                        "location": item.get("dtCode"),
                    }
                )
            return results

        # Live portal fallback 2: try /portal/meters/search
        try:
            raw = self._get("/portal/meters/search?q=&page=1")
            data = json.loads(raw)
            results = []
            for item in data.get("data", []):
                results.append(
                    {
                        "meter_id": item.get("meterId"),
                        "serial_number": item.get("serialNo"),
                        "status": item.get("installStatus") or "UNKNOWN",
                        "location": item.get("dtCode"),
                    }
                )
            if results:
                return results
        except Exception as exc:
            logger.debug("Internal meter search endpoint unavailable: %s", exc)

        return meters

    def get_meter_details(self, meter_id: str) -> dict:
        """Retrieve details for a single meter.

        Parses the detail HTML page. If the page returns 404 or missing content,
        raises MeterNotFoundError. Enriches data with internal energy telemetry
        if available.
        """
        path = self.settings.meter_detail_path.format(meter_id=meter_id)
        try:
            html = self._get(path)
        except PortalError as exc:
            if "404" in str(exc):
                raise MeterNotFoundError(f"Meter '{meter_id}' not found on the portal") from exc
            raise

        data = parsers.parse_meter_details(html, meter_id)
        if data is None:
            raise MeterNotFoundError(f"Meter '{meter_id}' not found on the portal")

        # Enrich with telemetry if available on the live portal
        try:
            energy_raw = self._get(f"/portal/meters/{meter_id}/energy")
            energy_items = json.loads(energy_raw).get("data", [])
            if energy_items:
                latest = energy_items[-1]
                if data.get("last_reading") is None:
                    data["last_reading"] = parsers.to_float(latest.get("kwh"))
                if data.get("last_reading_at") is None:
                    data["last_reading_at"] = parsers.to_datetime(latest.get("timestamp"))
        except Exception:
            pass

        return data

    def get_consumption(self, meter_id: str) -> dict:
        """Retrieve consumption history for a meter.

        Tries the HTML consumption page. If missing (as on the live portal),
        fetches from the internal energy telemetry endpoint (/portal/meters/{meter_id}/energy).
        """
        path = self.settings.consumption_path.format(meter_id=meter_id)
        try:
            html = self._get(path)
            data = parsers.parse_consumption(html, meter_id)
            if data and data.get("readings"):
                return data
        except PortalError as exc:
            if "404" not in str(exc):
                raise

        # Live portal fallback: internal energy endpoint
        energy_path = f"/portal/meters/{meter_id}/energy"
        try:
            raw = self._get(energy_path)
            json_data = json.loads(raw)
            readings = []
            for item in json_data.get("data", []):
                ts = parsers.to_datetime(item.get("timestamp"))
                kwh = parsers.to_float(item.get("kwh"))
                if ts is not None and kwh is not None:
                    readings.append({"timestamp": ts, "kwh": kwh, "unit": "kWh"})
            return {"meter_id": meter_id, "readings": readings}
        except PortalError as exc:
            if "404" in str(exc):
                raise MeterNotFoundError(f"Meter '{meter_id}' not found on the portal") from exc
            raise

    def get_hierarchy(self) -> dict:
        """Retrieve the meter network hierarchy tree (optional extension)."""
        try:
            html = self._get(self.settings.hierarchy_path)
            tree = parsers.parse_hierarchy(html)
            if tree and (tree.get("children") or tree.get("node_id") != "root"):
                return tree
        except PortalError as exc:
            if "404" not in str(exc):
                raise

        # Live portal fallback: build tree from transformers (/portal/dts)
        try:
            raw_dts = self._get("/portal/dts?page=1")
            dt_data = json.loads(raw_dts).get("data", [])
            feeder_map: dict = {}
            for dt in dt_data:
                f_code = dt.get("feederCode", "Unknown Feeder")
                if f_code not in feeder_map:
                    feeder_map[f_code] = {
                        "node_id": f_code,
                        "name": f"Feeder {f_code}",
                        "node_type": "feeder",
                        "children": [],
                    }
                feeder_map[f_code]["children"].append(
                    {
                        "node_id": dt.get("code"),
                        "name": f"{dt.get('name')} ({dt.get('code')})",
                        "node_type": "dt",
                        "children": [],
                    }
                )
            return {
                "node_id": "root",
                "name": "Urja Distribution Network",
                "node_type": "root",
                "children": list(feeder_map.values()),
            }
        except Exception:
            return {"node_id": "root", "name": "Network", "node_type": "root", "children": []}
