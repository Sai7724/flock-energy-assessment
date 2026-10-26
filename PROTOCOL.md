# Protocol Discovery: Legacy Urja Meter Ops Portal (`PROTOCOL.md`)

This document records the reverse-engineering analysis, network reconnaissance, authentication mechanics, internal API endpoints, and data anomalies discovered on the **Urja Meter Ops** legacy portal (`https://urja-ops.flockenergy.tech`).

---

## 1. Portal Architecture Overview

- **Host URL**: `https://urja-ops.flockenergy.tech`
- **Frontend Stack**: SvelteKit 2.x SPA / SSR with Vite and TailwindCSS.
- **Backend / Auth Stack**: Node.js runtime utilizing `better-auth` for session management and HMAC-SHA256 signature verification for protected export endpoints.
- **Application Paradigm**: Hybrid SSR/SPA.
  - Page routes (`/login`, `/meters`, `/meters/[id]`, `/transformers`) are served with basic HTML skeleton and meta tags.
  - While initial nameplate details are SSR-rendered inside `<dl>` tags on `/meters/[id]`, dynamic tables (meters list, distribution transformers, live telemetry, and consumption history) are hydrated client-side via asynchronous JSON API requests.

---

## 2. Authentication & Session Management

### 2.1 Login Workflow
- **Form Action Endpoint**: `POST /login`
- **Request Headers**:
  - `Content-Type`: `application/x-www-form-urlencoded`
  - `Origin`: `https://urja-ops.flockenergy.tech` *(Required by SvelteKit form action CSRF check; omitting causes HTTP 403 Forbidden)*
  - `Referer`: `https://urja-ops.flockenergy.tech/login`
- **Payload**:
  ```
  email=operator@urja.local&password=urja-ops-2026
  ```
  *(Note: The form field is named `email`, not `username`)*

### 2.2 Response Behavior & Nuances
- **Success Case**:
  - HTTP Status: `200 OK` (SvelteKit progressive enhancement action format).
  - Header: `Content-Type: application/json`
  - Cookie Set: `Set-Cookie: __Secure-better-auth.session_token=<token>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax`
  - JSON Body:
    ```json
    {
      "type": "redirect",
      "status": 303,
      "location": "/meters"
    }
    ```
  - *Critical Discovery*: Standard HTTP clients following traditional 302/303 redirect codes will not automatically redirect because the HTTP transport status code is `200 OK` with a JSON payload instructively specifying the redirect location.
- **Failure Case (Invalid Credentials)**:
  - HTTP Status: `200 OK`
  - Header: `Content-Type: application/json`
  - JSON Body:
    ```json
    {
      "type": "failure",
      "status": 401,
      "data": "[{\"email\":1,\"error\":2},\"operator@urja.local\",\"Invalid email or password.\"]"
    }
    ```
  - No `Set-Cookie` header is returned.

### 2.3 Session Expiration & Auto-Reauthentication
- **Unauthenticated Protected HTML Page (`GET /meters`)**:
  - Returns `302 Found` redirecting to `/login` (final HTTP 200 at `/login`).
- **Unauthenticated Internal API Request (`GET /portal/meters/search`)**:
  - Returns `401 Unauthorized` with JSON body:
    ```json
    {"error": "unauthorized", "message": "A valid session is required."}
    ```
- **Adapter Strategy**:
  - The client adapter monitors responses for both HTTP 401 status and redirects landing on `/login`.
  - When detected, it transparently executes a re-login flow using configured credentials and re-executes the original request without raising an unhandled exception to downstream consumers.

---

## 3. Discovered Endpoints & Protocol Specifications

Inspection of client bundles (`app.03ZTmlg3.js`, `nodes/4.2Bgc2kUI.js`, `nodes/5.C4dYcCRt.js`, `nodes/6.l777LXKL.js`) revealed several hidden internal JSON APIs:

### 3.1 Meter Search / Listing (`GET /portal/meters/search`)
- **URL**: `/portal/meters/search?q={query}&page={page}`
- **Authentication**: Session cookie `__Secure-better-auth.session_token`.
- **Query Parameters**:
  - `q`: Search string (filters by meter ID or serial number).
  - `page`: 1-based page index (defaults to 20 items per page).
- **Response Format**:
  ```json
  {
    "data": [
      {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Decommissioned",
        "dtCode": "DT-001"
      }
    ],
    "total": 403
  }
  ```

### 3.2 HMAC-Signed Full Database Export (`GET /portal/export`)
- **Key Retrieval URL**: `GET /portal/keys`
  - Returns: `{"data": {"signingSecret": "I3dZPPf5CgTp7JyGNMI8i6z8LFR7TmSR"}}`
- **Export URL**: `GET /portal/export?page=1`
- **Signing Protocol**:
  - Secret: `signingSecret`
  - Message Format: `"{HTTP_METHOD}\n{PATH}\n{QUERY}\n{TIMESTAMP}"` (e.g. `"GET\n/portal/export\npage=1\n1771926600"`)
  - Algorithm: HMAC-SHA256, hex-encoded digest.
  - Required Request Headers:
    - `x-timestamp`: Unix epoch in seconds.
    - `x-signature`: Hex-encoded HMAC-SHA256 digest.
- **Response Format**:
  - Returns the entire catalog of **403 smart meters** with complete metadata in a single request:
  ```json
  {
    "data": [
      {
        "meterId": "J100000",
        "serialNo": "SE33962",
        "make": "HPL",
        "phaseType": "single",
        "installStatus": "Decommissioned",
        "installType": "Whole Current",
        "build": "legacy",
        "dtCode": "DT-001",
        "hierarchy": {
          "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
          "circle": {"name": "Circle 1", "code": "C-01"},
          "division": {"name": "Division 1", "code": "D-01"},
          "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
          "substation": {"name": "Substation 1", "code": "SS-01"},
          "feeder": {"name": "Feeder 1", "code": "F-001"},
          "dt": {"name": "Malviya Nagar DT 1", "code": "DT-001"}
        },
        "geo": {
          "lat": 26.938961002479868,
          "lng": 75.83095696146852
        }
      }
    ],
    "total": 403
  }
  ```

### 3.3 Meter Detail (`GET /meters/{meterId}`)
- **URL**: `/meters/{meter_id}`
- **Response Format**: Server-rendered HTML containing:
  - Header & Breadcrumbs:
    ```html
    <nav class="mt-2 flex flex-wrap gap-1 text-xs text-slate-500">
      <span>Jaipur Zone 1 (Z-01)</span>
      <span class="text-slate-300">›</span>
      <span>Circle 1 (C-01)</span>
      ...
      <span>Malviya Nagar DT 1 (DT-001)</span>
    </nav>
    ```
  - Nameplate `<dl>` list:
    - `<dt>Meter ID</dt><dd>J100000</dd>`
    - `<dt>Serial No</dt><dd>SE33962</dd>`
    - `<dt>Make</dt><dd>HPL</dd>`
    - `<dt>Phase Type</dt><dd>single</dd>`
    - `<dt>Installation Status</dt><dd>Decommissioned</dd>`
    - `<dt>Installation Type</dt><dd>Whole Current</dd>`

### 3.4 Meter Location Telemetry (`GET /portal/meters/{meterId}/geo`)
- **URL**: `/portal/meters/{meterId}/geo`
- **Response Format**:
  ```json
  {
    "data": {
      "latitude": "26.938961002479868",
      "longitude": "75.83095696146852"
    }
  }
  ```

### 3.5 Meter Consumption / Energy Telemetry (`GET /portal/meters/{meterId}/energy`)
- **URL**: `/portal/meters/{meterId}/energy`
- **Response Format**:
  ```json
  {
    "data": [
      {
        "timestamp": "23/06/2026 23:30",
        "kwh": "48438.74",
        "kvah": "52313.84",
        "voltR": "226"
      },
      {
        "timestamp": "24/06/2026 00:00",
        "kwh": "48439.16",
        "kvah": "52314.29",
        "voltR": "229"
      }
    ]
  }
  ```

### 3.6 Distribution Transformers (`GET /portal/dts`)
- **URL**: `/portal/dts?page={page}`
- **Response Format**:
  ```json
  {
    "data": [
      {
        "code": "DT-001",
        "name": "Malviya Nagar DT 1",
        "feederCode": "F-001",
        "capacityKva": 100
      }
    ],
    "total": 20
  }
  ```

---

## 4. Discovered Anomalies & Data Irregularities

| Area | Anomaly Description | Solution / Normalization Strategy |
| :--- | :--- | :--- |
| **Login Redirect** | SvelteKit returns `200 OK` with JSON `{type: "redirect", status: 303}` instead of HTTP 303 redirect. | Inspect JSON body and `__Secure-better-auth.session_token` cookie rather than relying solely on HTTP redirect codes. |
| **Empty HTML Table** | `GET /meters` returns static HTML containing `<tr><td>No meters found.</td></tr>`. Data is fetched via client-side fetch. | Filter out placeholder rows in HTML parser and seamlessly fall back to `/portal/export` or `/portal/meters/search`. |
| **Timestamp Formatting** | Energy consumption returns timestamps formatted as `DD/MM/YYYY HH:MM` (e.g. `"23/06/2026 23:30"` without seconds) and numeric strings for floats. | Augmented parser datetime formats with `"%d/%m/%Y %H:%M"` and coerce `"48438.74"` to Python float `48438.74`. |
| **Non-existent Path** | Documented `/meters/{id}/consumption` returns `404 Not Found` on the live portal. | Adapter automatically diverts to the internal `/portal/meters/{id}/energy` endpoint and maps readings into the API's `ConsumptionHistory` model. |
| **Hierarchy Path** | Documented `/hierarchy` returns `404 Not Found`. | Adapter aggregates transformer data from `/portal/dts` and export hierarchy trees to construct a nested `HierarchyNode` hierarchy. |
| **CSRF / Origin Check** | POST requests without an explicit `Origin` header matching the base URL receive `403 Forbidden`. | Adapter always sets `Origin` and `Referer` headers on authentication POSTs. |
