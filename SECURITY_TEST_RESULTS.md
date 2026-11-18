# Security Test Results — FlightOps Insight

**Date:** 2026-11-18
**Commit:** `f278961` + uncommitted hardening changes
**Environment:** Windows 11, Python 3.12.10, `.venv`, Streamlit 1.67.0, DuckDB 2.0.0
**Deployment under test:** local / internal, loopback bind, no TLS terminator

> **Read this first.** Only the rows marked **Tested** below are evidence-backed.
> Everything marked **Not tested** is an *unverified* control. No penetration test,
> no TLS scan and no third-party IdP login was performed, so this document must not be
> read as a clean bill of health.

---

## 1. Result summary

| # | Control | Method | Result | Status |
|---|---|---|---|---|
| 1 | Application regression suite | `pytest` | **58 passed**, 0 failed | **Tested** |
| 2 | Security unit/regression tests | `pytest tests/test_security.py` | **24 passed** | **Tested** |
| 3 | Static analysis (medium+) | `bandit` with reviewed inline suppressions | exit **0**, no findings | **Tested** |
| 4 | Known-vulnerable dependencies | `pip-audit` | **No known vulnerabilities found** | **Tested** |
| 5 | Server binds to loopback | live HTTP probe | reachable on `127.0.0.1`/`localhost` only | **Tested** |
| 6 | Health endpoint | live HTTP probe | `200`, body `ok` | **Tested** |
| 7 | XSRF cookie | live HTTP probe | `SameSite=Lax`, `Path=/` | **Tested** |
| 8 | Cross-origin WebSocket rejected | live WS probe | foreign + `null` origin → **403** | **Tested** |
| 9 | Agent API disabled | live HTTP probe | `403 not_available`; schema says `disabled` | **Tested** |
| 10 | Auth fail-closed switch | `AppTest` + `FLIGHTOPS_REQUIRE_AUTH=1` | blocks render, clear error | **Tested** |
| 11 | Apps render without auth (default) | `AppTest` | OTP overview, no exception | **Tested** |
| 12 | HTML/Markdown escaping | unit tests | payloads neutralised | **Tested** |
| 13 | SQL dimension allowlist | unit tests | hostile input rejected pre-query | **Tested** |
| 14 | Config file validity for Streamlit | unit test via `streamlit.config` | parses, options read correctly | **Tested** |
| 15 | OIDC login end-to-end | real IdP | — | **Not tested** |
| 16 | TLS / HSTS / HTTPS redirect | TLS scanner | — | **Not tested** (no TLS in scope) |
| 17 | CSP / `X-Content-Type-Options` / `Referrer-Policy` / `Permissions-Policy` / frame-ancestors | header probe | **absent** from Streamlit responses | **Not implemented** (see H-06) |
| 18 | OWASP ZAP baseline/active scan | ZAP | — | **Not tested** |
| 19 | Rate limiting / brute force | load test | — | **Not implemented** (local scope) |
| 20 | FastAPI `/predict` auth, validation, size limits | service probe | — | **Not tested** (out of scope) |
| 21 | Container build / image scan | docker build + trivy | — | **Not tested** (out of scope) |
| 22 | Dependency pinning / lockfile | review | ranges, no hash lock | **Open** |

---

## 2. Automated test suite

### Command

```powershell
.\.venv\Scripts\python.exe -m pytest
```

### Actual output (tail)

```text
.........................................................                [100%]
57 passed in 4.97s
```

Re-run after the final config change:

```text
..........................................................               [100%]
58 passed in 5.07s
```

### Security subset

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_security.py -q
```

```text
........................                                                 [100%]
tests/test_security.py: 24
```

**What this proves:** no regression in feature engineering, data contracts, live
tracking, optimization or the new security helpers. It does **not** prove the absence of
security bugs; it covers the specific behaviours listed in §3.

---

## 3. Security tests and what each one covers

`tests/test_security.py` — 24 tests.

| Test class | Assertions | Security property |
|---|---|---|
| `escape_html` | `<script>` and `"` are encoded; `None` → `""` | HTML-injection sink is neutralised |
| `escape_markdown` | `[click](…)` becomes literal text; control chars stripped | Links/emphasis injection via third-party callsigns |
| `mask_email` | `analyst@example.com` → `a***@example.com`; bad input → placeholder | No full address in UI/log-adjacent output |
| `env_requires_auth` | `1/true/yes/on` → `True`; `""/0/false/no/off/None` → `False` | Fail-closed switch cannot be bypassed by casing/whitespace |
| `_SEGMENT_DIMENSIONS` | allowlist contains the expected columns | Explicit, reviewable SQL identifier set |
| `_segment_query` hostile input | `carrier; DROP TABLE flights`, `carrier) --`, `unknown_column`, `1; SELECT …` all raise `ValueError` | SQL identifier injection fails **before** any SQL is built |
| `test_streamlit_config_keeps_hardened_defaults` | loopback address, CORS/XSRF on, static serving off, upload ≤ 10 MB, host allowlist, `showErrorDetails="none"`, telemetry off, agent API off, empty `trustedUserHeaders` | Config cannot silently regress |
| `test_streamlit_parser_accepts_hardened_config` | `streamlit.config.get_option(...)` returns the hardened values | The file is valid for Streamlit itself, not just `tomllib` |

> The last test exists because an earlier draft set `trustedUserHeaders = []`, which
> `tomllib` accepts but Streamlit rejects (`'list' object has no attribute 'values'`).
> A config file that crashes the server is itself an availability defect, so validity is
> now asserted through Streamlit's parser.

---

## 4. Static analysis

### Command

```powershell
.\.venv\Scripts\python.exe -m bandit -r src deployment -ll -q
```

### Actual result

```text
bandit exit: 0
```

### Reviewed inline suppressions (6 lines)

| Location | Why accepted |
|---|---|
| `analysis/otp_insights.py` (`"SELECT "` fragment) | `dimension` is checked against `_SEGMENT_DIMENSIONS` before the query is built; `min_flights` is a bound `?` parameter |
| `data/database.py` (`CREATE VIEW … read_parquet`) | DuckDB path comes from `config.yaml`, not request data |
| `data/sqlite_backend.py` ×5 (uncommitted, in-progress feature) | object/column names come from the catalog or a fixed schema tuple |

Why not a baseline file: bandit compares baseline filenames byte-for-byte, so a baseline
generated on Windows (`src\…`) never matches the Linux CI runner (`src/…`) and the job
would fail. Inline `# nosec` is cross-platform and keeps the reason beside the code.

**What this proves:** no medium+ static finding is unsuppressed. It does **not** prove
the suppressed lines are unexploitable forever; each suppression has a written reason and
the allowlist is unit-tested.

### Before/after

| Finding | Before | After |
|---|---|---|
| B324 MD5 "for security" | 1 high | fixed (`usedforsecurity=False`) |
| B608 SQL construction | 7 | 6 reviewed inline suppressions; `otp_insights` now allow-listed + parameterised |

---

## 5. Dependency audit

### Command

```powershell
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt --progress-spinner off
```

### Actual result

```text
No known vulnerabilities found
pip-audit exit: 0
```

Audited against `requirements.txt` (including the newly added `Authlib>=1.3.2,<2`).
**What this proves:** no *advisory-known* vulnerability in the declared version ranges at
scan time. It does **not** prove the installed environment is clean, and a future
advisory can invalidate this result — hence the CI job and Dependabot.

---

## 6. Live HTTP and WebSocket tests

Server started with the repository config on port 8502:

```powershell
python -m streamlit run app.py --server.port 8502
```

### 6.1 Availability and binding

| Request | Observed | Expected |
|---|---|---|
| `GET http://127.0.0.1:8502/_stcore/health` | `200`, body `ok` | ✅ |
| `GET http://127.0.0.1:8502/` | `200`, `text/html`, 7863 bytes | ✅ |
| `GET http://localhost:8502/` | `200` | ✅ |
| Bind address | `127.0.0.1:8502` (from server log) | ✅ loopback only |

### 6.2 Response headers (actual)

```text
HTTP/1.1 200 OK
server: uvicorn
content-type: text/html; charset=utf-8
accept-ranges: bytes
content-length: 7863
last-modified: Fri, 06 Nov 2026 15:08:32 GMT
etag: "f10c3f7762aad179332f0cd2430d7e5d"
cache-control: no-cache
```

Header probe result:

| Header | Present |
|---|---|
| `Content-Security-Policy` | ❌ absent |
| `Strict-Transport-Security` | ❌ absent |
| `X-Content-Type-Options` | ❌ absent |
| `Referrer-Policy` | ❌ absent |
| `Permissions-Policy` | ❌ absent |
| `X-Frame-Options` | ❌ absent |
| `Access-Control-Allow-Origin` | absent on every probed response |

**Interpretation:** Streamlit emits none of the browser-hardening headers. Absence of
`Access-Control-Allow-Origin` is *protective* (browsers cannot read cross-origin
responses), but the missing headers are a real gap for any networked deployment → H-06
remains open. This was not "fixed" and is not claimed to be.

### 6.3 Host header handling

| Request | Observed |
|---|---|
| `GET /` with `Host: evil.example` | **200** |
| `POST /_stcore/agent/interact` with `Host: evil.example` | not reached (API disabled) |

**Interpretation:** `server.allowedHosts` does **not** filter the static HTML/index
route in 1.67; it governs the WebSocket handshake and the agent API. The practical
mitigation for a networked deployment is a reverse proxy that rejects unknown `Host`
values. Loopback binding is what protects the current deployment. Documented, not fixed.

### 6.4 Cross-origin WebSocket (the real CORS control)

Probe with `websockets` 17.4 against `ws://127.0.0.1:8502/_stcore/stream`:

| Origin sent | Observed |
|---|---|
| `http://localhost:8502` | **CONNECTED** |
| `http://127.0.0.1:8502` | **CONNECTED** |
| `http://evil.example` | **REJECTED** — HTTP 403 |
| `null` | **REJECTED** — HTTP 403 |
| *(no Origin header)* | CONNECTED |

**Interpretation:** the cross-origin control is enforced on the WebSocket handshake, not
via `Access-Control-Allow-Origin`. A hostile page cannot open a session, which closes the
cross-site WebSocket-hijacking path. Non-browser clients that omit `Origin` still connect
— by design, and irrelevant here because the server is loopback-only. Note that
`localhost:8502` was accepted even though the file lists port 8501: Streamlit also allows
same-origin requests (origin host == `Host`).

### 6.5 Cookie attributes

```text
set-cookie: _streamlit_xsrf=…; Path=/; SameSite=Lax
```

`SameSite=Lax` blocks the cookie on cross-site subrequests. **No `HttpOnly` and no
`Secure` flag** — expected because the session cookie is script-managed and there is no
TLS. Add `Secure` at the proxy layer when HTTPS exists; do not claim it today.

### 6.6 Experimental agent API

Streamlit 1.67 registers three agent routes even when the API is off:
`/_stcore/agent/interact`, `/_stcore/agent/openapi.json`, `/_stcore/agent/mcp`.

| Request | Observed |
|---|---|
| `POST /_stcore/agent/interact` with `{}` | `403` — `"code":"not_available"` |
| `GET /_stcore/agent/openapi.json` | `200` — `"x-streamlit-agent-api":"disabled"` |
| Config | `server.enableAgentApi = False` (pinned) |

**Interpretation:** the programmatic interaction surface is **off and pinned off** in
`.streamlit/config.toml`. This was verified at runtime, not merely inferred from the
default.

---

## 7. Streamlit functional tests (`AppTest`)

### 7.1 Default (no provider configured)

```text
default run -> exceptions: [] | errors: [] | title: ['OTP overview']
```

**Interpretation:** the auth gate is a no-op when unconfigured, so the prototype still
opens directly. Escaping changes did not break rendering, and no exception reached the
UI.

### 7.2 Fail-closed switch

```powershell
$env:FLIGHTOPS_REQUIRE_AUTH = "1"
```

```text
fail-closed -> exceptions: [] | errors: ['Authentication is required for this deployment, but no OIDC provider is configured. Add an [auth] section to .streamlit/secrets.toml.'] | titles: []
```

Server log (no PII):

```text
21:12:13 | ERROR | flightops.app.auth | Authentication required but no OIDC provider is configured
```

**Interpretation:** with the switch on and no provider, **no page renders** and the user
sees a generic configuration message — no traceback, no path, no stack. This is the
behaviour a shared deployment must have.

### 7.3 Not covered by `AppTest`

- A successful OIDC round trip, token validation, cookie signing and logout.
- Sidebar identity rendering with real claims.
- Multi-page navigation under an authenticated session.

---

## 8. Checks that were performed by inspection only

These are **reasoned**, not executed:

| Check | Basis |
|---|---|
| No hard-coded credentials in tracked files | pattern scan of textual tracked files and a git-history spot check |
| Zip extraction cannot traverse paths | `zipfile.ZipFile.open(member)` streaming; no `extractall` |
| `torch.load` cannot execute arbitrary pickles | `weights_only=True` on both calls |
| DuckDB is read-only | `duckdb.connect(..., read_only=True)` |
| User filters are parameterised | `?` placeholders in `data_access.py` |
| No user-controlled URL is fetched | outbound hosts are constants in config |

Inspection is weaker than a test. Treat these as **unverified**.

---

## 9. Not tested — and why

| Area | Reason |
|---|---|
| **OWASP ZAP** | No HTTP proxy/TLS target in scope; a baseline scan of loopback Streamlit mostly reports framework-generated HTML and would not exercise the auth/API paths. Run it after the reverse proxy exists. |
| **TLS / HSTS / redirect** | Nothing terminates TLS in this deployment. Enabling HSTS on `localhost` is harmful. |
| **CSP** | Streamlit needs inline script/style and `wss:`; a policy cannot be validated without a browser-driven page walk. Template is report-only. |
| **OIDC end-to-end** | No IdP tenant or client credentials were provided. The gate logic is tested; the protocol exchange with a real provider is not. |
| **Brute force / rate limiting** | No credential form exists (OIDC) and no rate limiter is implemented. |
| **IDOR / access control bypass** | No per-user objects or write operations exist to attack. |
| **File upload** | No upload feature (`st.file_uploader` absent). |
| **FastAPI `/predict`** | Explicitly out of scope in this pass; service is not started by `app.py`. |
| **Container build / image scan** | Deployment assets out of scope. |
| **Destructive testing** | Not authorised and unnecessary for a read-only prototype. |

---

## 10. Reproduce everything

```powershell
# 1. Tests
.\.venv\Scripts\python.exe -m pytest

# 2. Static analysis
.\.venv\Scripts\python.exe -m bandit -r src deployment -ll -q

# 3. Dependency audit
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt --progress-spinner off

# 4. Header / binding probe
Start-Process .\.venv\Scripts\python.exe `
  -ArgumentList "-m","streamlit","run","app.py","--server.port","8502"
Start-Sleep 12
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8502/_stcore/health
curl.exe -s -D - -o NUL http://127.0.0.1:8502/

# 5. WebSocket origin probe (needs `websockets`)
.\.venv\Scripts\python.exe - <<'PY'
import asyncio, websockets
async def main():
    for origin in ["http://localhost:8502", "http://evil.example"]:
        try:
            async with websockets.connect(
                "ws://127.0.0.1:8502/_stcore/stream",
                additional_headers={"Origin": origin}, open_timeout=8):
                print(origin, "CONNECTED")
        except Exception as exc:
            print(origin, "REJECTED", type(exc).__name__)
asyncio.run(main())
PY

# 6. Auth paths
.\.venv\Scripts\python.exe -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('app.py'); at.run(); print(at.exception, at.error, [t.value for t in at.title])"
$env:FLIGHTOPS_REQUIRE_AUTH="1"
.\.venv\Scripts\python.exe -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('app.py'); at.run(); print(at.exception, at.error, [t.value for t in at.title])"
```

---

## 11. Honest conclusion

- **Verified working:** loopback binding, XSRF cookie, cross-origin WebSocket rejection,
  agent API off, auth fail-closed, escaping, SQL allowlist, config validity, no known
  vulnerable declared dependencies, no new static findings.
- **Verified broken/absent:** no HTTP security headers, no TLS, no CSP, no rate limiting,
  `Host` is not filtered on the index route, no OIDC round trip proven.
- **Highest residual risk today:** none of the above protects a deployment that is bound
  to `0.0.0.0` without a proxy. The loopback bind is the control that matters; everything
  else is defence in depth around it.
