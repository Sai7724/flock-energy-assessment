# Reflection & Engineering Analysis (`REFLECTION.md`)

This document provides a comprehensive retrospective and engineering analysis for the **Flock Energy - Urja Meter Ops API** wrapper project.

---

## 1. Assumptions Made

1. **Legacy Portal Application Architecture**:
   - *Initial Assumption*: We initially assumed the legacy portal was a classic multi-page application (MPA) returning server-rendered HTML tables with traditional HTTP 302 redirects upon authentication.
   - *Reality Discovered*: The portal is a modern SvelteKit hybrid SSR/SPA. While route skeletons are server-rendered, data tables are hydrated client-side via asynchronous JSON fetches (`/portal/meters/search`, `/portal/meters/{id}/energy`, `/portal/dts`).
   - *Resolution*: Designed a hybrid adapter that parses static HTML structures (for mock fixtures and SSR elements like nameplates) while gracefully utilizing internal JSON APIs when dynamic data hydration is detected.

2. **Session Persistence & Scope**:
   - *Assumption*: We assumed a single persistent session shared across requests is suitable for the API wrapper instance.
   - *Implications*: For single-process deployments and low-to-medium throughput, this is optimal because it eliminates redundant login calls. In a distributed multi-tenant deployment, sessions should be keyed per operator or isolated in a connection pool.

3. **Data Type Consistency**:
   - *Assumption*: We assumed that numeric readings (kWh, voltage) might appear wrapped in text units or currency prefixes, and dates could appear in multiple regional variations.
   - *Resolution*: Implemented defensive normalization helpers (`to_float`, `to_datetime`) with comprehensive format masks and regular expressions to prevent serialization failures.

---

## 2. Difficult Parts & Roadblocks Encountered

### 2.1 SvelteKit Action Redirect Protocol
- **Problem**: When `POST /login` was submitted with valid credentials, `httpx` did not automatically redirect to `/meters`. Instead, the portal responded with an HTTP `200 OK` and Content-Type `application/json` containing:
  ```json
  {"type": "redirect", "status": 303, "location": "/meters"}
  ```
  along with `Set-Cookie: __Secure-better-auth.session_token=...`.
- **Roadblock**: The client adapter's initial code checked `if response.url.path != "/login": return True`. Because the HTTP transport status was 200 rather than an HTTP redirect, `response.url.path` remained `/login`, causing `client.login()` to return `False` despite valid session cookies.
- **Resolution**: Updated `UrjaPortalClient.login()` to inspect both the SvelteKit JSON response payload (`body.get("type") == "redirect"`) and cookie jar state (`__Secure-better-auth.session_token` or `session_id`).

### 2.2 Dynamic Client-Side Table Hydration
- **Problem**: Querying `GET /meters` on the live portal returned an HTML table with a single placeholder row:
  ```html
  <tr><td colspan="6" class="px-4 py-6 text-center text-slate-400">No meters found.</td></tr>
  ```
  The naive HTML table parser extracted `"No meters found."` as a legitimate meter with `meter_id = "No meters found."`.
- **Roadblock**: DevTools inspection revealed that meters were actually loaded via JavaScript from `/portal/meters/search?q=&page=1`.
- **Resolution**:
  1. Updated `_parse_meters_table` to detect and filter out placeholder rows like `"No meters found."`.
  2. Implemented a fallback mechanism in `get_meters()`: when the parsed HTML table is empty, the client queries the internal API or the HMAC-signed `/portal/export` endpoint.

### 2.3 Reverse-Engineering the Signed Export Endpoint
- **Problem**: Paginating through `/portal/meters/search` required multiple sequential HTTP calls (21 pages for 403 meters).
- **Roadblock**: Inspecting Vite chunks (`nodes/4.2Bgc2kUI.js`) revealed a hidden export feature requiring HMAC-SHA256 request signatures:
  `GET\n/portal/export\npage=1\n{timestamp}` signed with a key retrieved from `/portal/keys`.
- **Resolution**: Reverse-engineered the HMAC signing protocol and implemented `_get_export_data()` in Python, allowing all 403 meters with complete geo and hierarchy information to be retrieved in a single request in under 0.5 seconds.

### 2.4 Non-Existent Documented Endpoints
- **Problem**: The brief suggested endpoints like `/meters/{id}/consumption` and `/hierarchy`. On the live portal, both returned HTTP 404.
- **Resolution**:
  - For consumption: Redirected to `/portal/meters/{id}/energy` which supplies detailed time-series energy readings.
  - For hierarchy: Queried `/portal/dts` and export metadata to assemble an organized tree structure of feeders, distribution transformers, and meters.

---

## 3. Mistakes Occurred & How They Were Resolved

1. **Mistake: Premature Assumption of Standard HTTP Redirects**
   - *What Happened*: The adapter initially looked solely at `response.url.path != login_path`.
   - *Impact*: Failed login against both live portal and mock tests with 200 OK responses.
   - *Fix*: Handled JSON redirects and session cookie presence in `app/client.py`.

2. **Mistake: Overwriting Values with Defaults Before Fallback**
   - *What Happened*: In `parse_meter_details()`, `data["status"]` was assigned `"UNKNOWN"` as a default before checking definition list `<dl>` elements:
     ```python
     if not any(v is not None for k, v in data.items() if k != "meter_id"):
         data.update(_parse_detail_definition_list(soup))
     ```
     Because `"UNKNOWN"` is not `None`, the condition was never met, and `<dl>` fields like `Serial No` and `Installation Status` were missed.
   - *Impact*: Serial numbers returned `None` from the live portal HTML.
   - *Fix*: Unconditionally merged `_parse_detail_definition_list(soup)` and updated key normalization mapping for `"serial no"`, `"installation status"`, etc.

3. **Mistake: Swallowing Critical Errors in Fallback Handlers**
   - *What Happened*: When adding live fallbacks to `get_meters()`, `except PortalError:` caught both 404s and `AuthenticationError` / 5xx server errors.
   - *Impact*: Swallowed re-authentication failures and server errors during testing.
   - *Fix*: Refined exception handling to inspect error status codes (`if "404" not in str(exc): raise`), ensuring authentication errors and 5xx errors bubble up as expected.

---

## 4. Architectural Trade-offs & Decisions

| Decision | Trade-off Made | Rationale |
| :--- | :--- | :--- |
| **Hybrid Scraper + Internal API Adapter** | Increases adapter complexity compared to a pure scraper or pure API client. | Maximum robustness: preserves backward compatibility with mock HTML fixtures while unlocking full real-time data from the live SvelteKit portal. |
| **HMAC-Signed Bulk Export** | Relies on internal HMAC signing logic that could change if upstream updates keys. | Massive performance gain (0.46s for all 403 meters vs ~3s for 21 paginated calls). Includes fallback to paginated search if signing ever fails. |
| **In-Memory Lifespan Client** | Reuses a single `httpx.Client` session instance across FastAPI requests. | Avoids redundant login overhead on every request while maintaining session cookies in memory. |
| **Pure Parsing Functions** | Parsers in `app/parsers.py` are strictly stateless and accept raw strings/HTML. | Allows unit testing of HTML parsing logic in total isolation without network dependencies or mocks. |

---

## 5. Future Improvements & Production Readiness

1. **Distributed Session Storage (Redis)**:
   - For horizontal scaling across multiple Kubernetes pods, persist session cookies and CSRF tokens in a centralized Redis store rather than in-memory client state.

2. **Per-Tenant / Per-Caller Session Isolation**:
   - Implement a session pool keyed by downstream authorization tokens to support multi-tenant operational monitoring.

3. **In-Memory TTL Caching**:
   - Cache meter listings and hierarchy nodes with a short TTL (e.g. 60–120 seconds) using `cachetools` or Redis to protect the legacy portal from high request volumes.

4. **Async/Await Migration**:
   - Migrate `httpx.Client` to `httpx.AsyncClient` with async parser execution to increase throughput under high concurrent I/O load.

5. **Circuit Breaker Pattern**:
   - Integrate circuit breaker protection (e.g. using `tenacity` or `pybreaker`) to automatically back off if the upstream legacy portal experiences high latency or outages.
