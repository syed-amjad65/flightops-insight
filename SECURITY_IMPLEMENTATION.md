# Security Implementation — FlightOps Insight

**Date:** 2026-11-18
**Scope:** Streamlit app, configuration, dependency/CI scanning, documentation.
**Deployment target:** local / internal, optional OIDC.
**Constraint honoured:** no functionality removed, no UI redesign, no database migration, no deployment performed.

---

## 1. Design decisions

| Decision | Rationale |
|---|---|
| **OIDC instead of local passwords** | Streamlit 1.67 has native `st.login`/`st.user`. Delegating to an IdP removes the need to store passwords, implement MFA/reset/lockout, or build a session store. |
| **Authentication is opt-in** | A workstation prototype must keep working. A complete `[auth]` block in `secrets.toml` enables the gate; `FLIGHTOPS_REQUIRE_AUTH=1` (or `app.require_auth: true`) makes it fail closed. |
| **Loopback binding by default** | `server.address = "127.0.0.1"` means the app is unreachable from the LAN even without login. This is the primary control for the current deployment mode. |
| **Tracebacks disabled in the browser** | Keeps paths, stack frames and library internals server-side. Full details still go to `reports/logs/flightops.log`. |
| **Escape at the sink** | Third-party ADS-B strings pass through `escape_html` / `escape_markdown` before any HTML/Markdown sink. |
| **No new framework or proxy in this pass** | Reverse proxy/TLS is only meaningful when a domain and HTTPS exist; documented as a template instead of being deployed blind. |
| **Reviewed inline suppressions, not a path-based baseline** | Bandit matches baseline filenames exactly, so a Windows-generated baseline breaks on the Linux CI runner. Unavoidable dynamic SQL identifiers carry `# nosec` with the reason in the comment above; CI fails on any new finding. |

---

## 2. Files created

### `.streamlit/config.toml`
Pins Streamlit's security-relevant defaults and adds explicit restrictions:

```toml
[server]
address = "127.0.0.1"
enableCORS = true
corsAllowedOrigins = ["http://localhost:8501", "http://127.0.0.1:8501"]
enableXsrfProtection = true
allowedHosts = ["localhost", "127.0.0.1"]
enableAgentApi = false
trustedUserHeaders = {}
maxWidgetStateSize = 25
enableStaticServing = false
maxUploadSize = 5
maxMessageSize = 50

[browser]
gatherUsageStats = false

[client]
showErrorDetails = "none"
toolbarMode = "minimal"
```

### `.streamlit/secrets.toml.example`
Template for the optional OIDC provider. Contains no secrets. The real `.streamlit/secrets.toml` remains git-ignored.

### `src/flightops/security.py`
Dependency-free helpers:
- `escape_html(value)` — HTML-escapes text (including quotes) for `unsafe_allow_html` sinks.
- `escape_markdown(value)` — escapes Markdown metacharacters and strips control characters so ADS-B callsigns cannot become links/emphasis.
- `mask_email(value)` — `a***@example.com` for display/log-adjacent use.

### `src/flightops/app/auth.py`
- `auth_configured()` — true only when `[auth]` has `client_id`, `client_secret`, `server_metadata_url`, `redirect_uri`, `cookie_secret`.
- `requires_auth(cfg)` — env var or `app.require_auth`.
- `require_user(cfg)` — renders the login screen and calls `st.stop()` when configured; fails closed with a clear error when auth is required but unconfigured.
- `render_identity()` — sidebar identity and explicit sign-out.
- Logs "session established"/"signed out" **without** email, token or subject identifiers.

### `tests/test_security.py`
24 tests covering escaping, email masking, the fail-closed switch, the SQL dimension
allowlist, and two config regression checks. One of those parses `.streamlit/config.toml`
through `streamlit.config` itself, because `tomllib` alone accepted a `trustedUserHeaders`
shape that crashed the server.

### Agent API and identity headers
Streamlit 1.67 registers an experimental programmatic API (`/_stcore/agent/interact`,
`/mcp`, `/openapi.json`). It is off by default; `.streamlit/config.toml` now pins
`enableAgentApi = false`, and the runtime probe confirms `403 not_available` with the
schema reporting `disabled`. `trustedUserHeaders` is pinned to an empty table so the app
can never derive `st.user` from client-supplied headers.

### CI and supply chain
- `.github/dependabot.yml` — weekly pip updates, monthly GitHub Actions updates.
- `.github/workflows/ci.yml` — new `security` job: `pip-audit -r requirements.txt` and
  `bandit -r src deployment -ll`; the SQL lines that must interpolate an allow-listed
  identifier carry an inline `# nosec` with a comment explaining why.

---

## 3. Files modified

| File | Change | Security effect |
|---|---|---|
| `src/flightops/app/app.py` | Calls `auth.require_user(get_config())` after `set_page_config`; renders identity | Optional login gate, no page renders before authentication |
| `src/flightops/app/common.py` | `page_header` and `assumption_box` escape their text; `so_what` documents that it accepts trusted markup only | Removes HTML injection through help text; makes the trust contract explicit |
| `src/flightops/app/pages/live_tracking.py` | Escapes `origin_country` in the call-out; escapes callsign/ICAO/country/phase/squawk in the detail caption; generic provider/map errors with server-side logging | Closes the third-party HTML/Markdown injection path; stops provider internals leaking to users |
| `src/flightops/app/pages/predictor.py` | Generic model-load error + `logger.exception` | No traceback/path leakage in the UI |
| `src/flightops/analysis/otp_insights.py` | `_SEGMENT_DIMENSIONS` allowlist; `min_flights` bound as a `?` parameter | SQL text can no longer be influenced by a caller-supplied dimension |
| `src/flightops/data/aircraft.py` | `hashlib.md5(..., usedforsecurity=False)` | Silences a false-positive crypto finding; documents that the hash is for deterministic sampling only |
| `config.yaml` | `app.require_auth: false` | Explicit, reviewable fail-closed switch |
| `requirements.txt`, `pyproject.toml` | Added `Authlib>=1.3.2,<2` | Required by `st.login`; keeps the OIDC path functional |
| `.github/workflows/ci.yml` | Added dependency audit + SAST job | Detects known-vulnerable dependencies and new insecure patterns |
| `src/flightops/data/database.py` | Inline `# nosec` on the config-driven DuckDB view path | Unblocks the gate without hiding new findings |
| `src/flightops/analysis/otp_insights.py` (build) | SQL assembled as explicit fragments with an inline `# nosec` on the allow-listed identifier | Same, and the reason is visible at the sink |

**Verified:** all 58 tests pass and both app paths were executed with `streamlit.testing.v1.AppTest`.
**Unverified:** end-to-end OIDC with a real IdP, TLS/HSTS/CSP, container build.

---

## 4. Configuration changes at a glance

| Setting | Before | After | Why |
|---|---|---|---|
| `server.address` | unset (all interfaces) | `127.0.0.1` | Loopback only |
| `server.enableCORS` | default `true` | pinned `true` + allowed origins | Prevent silent loosening; block foreign origins |
| `server.enableXsrfProtection` | default `true` | pinned `true` | CSRF protection |
| `server.allowedHosts` | empty (any Host) | `localhost`, `127.0.0.1` | DNS-rebinding protection for WebSocket (the index route is not filtered; see audit) |
| `server.enableAgentApi` | off by default | pinned `false` | No programmatic HTTP/MCP interaction surface |
| `server.trustedUserHeaders` | empty | pinned `{}` | Cannot trust spoofable identity headers |
| `server.maxWidgetStateSize` | 25 MB | pinned 25 MB | Bounds widget-state bodies |
| `server.enableStaticServing` | `false` | pinned `false` | No unintended static file serving |
| `client.showErrorDetails` | `full` | `none` | No tracebacks in the browser |
| `browser.gatherUsageStats` | `true` | `false` | No telemetry |
| Upload/message size | 200 MB defaults | 5 MB / 50 MB | Reduce abuse surface |
| `app.require_auth` | n/a | `false` (opt-in) | Fail-closed available without breaking local use |

---

## 5. Commands

```powershell
# Install (Authlib is required only for OIDC)
python -m pip install -r requirements.txt

# Run locally (now loopback-only)
python -m streamlit run app.py

# Tests
python -m pytest -q

# Security scans (same as CI)
python -m pip install "pip-audit>=2.7,<3" "bandit>=1.7,<2"
python -m pip_audit -r requirements.txt --progress-spinner off
python -m bandit -r src deployment -ll -q

# Enable authentication (after creating .streamlit/secrets.toml)
$env:FLIGHTOPS_REQUIRE_AUTH = "1"
python -m streamlit run app.py
```

Generate a cookie secret:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 6. Rollback

Targeted revert, preserving unrelated work:

```powershell
git checkout -- `
  .streamlit/config.toml `
  src/flightops/app/app.py `
  src/flightops/app/common.py `
  src/flightops/app/pages/live_tracking.py `
  src/flightops/app/pages/predictor.py `
  src/flightops/analysis/otp_insights.py `
  src/flightops/data/aircraft.py `
  config.yaml requirements.txt pyproject.toml `
  .github/workflows/ci.yml

Remove-Item src/flightops/security.py, src/flightops/app/auth.py,
  .streamlit/secrets.toml.example, .github/dependabot.yml, tests/test_security.py
```

Do **not** use `git reset --hard`: the working tree contains unrelated in-progress work.

---

## 7. Deliberately not changed

- OpenSky fetch logic and 30 s cache (behaviour preserved).
- Database schema, DuckDB paths and all generated artifacts.
- Model files, training scripts and optimization code.
- FastAPI service, Dockerfile, Databricks/Vertex templates (documented as open items).
- UI layout, styling and page structure.

---

## 8. Conflicts and mitigations

| Conflict | Mitigation |
|---|---|
| Strict CSP breaks Streamlit inline scripts/styles and WebSockets | Not enabled on loopback. When TLS is added, deploy CSP in report-only mode first; template in `SECURITY_CONFIGURATION.md`. |
| HSTS on `localhost`/IP can break local development | HSTS is documented for a real HTTPS hostname only, never for loopback. |
| Binding `0.0.0.0` is required inside containers | Keep loopback for workstation use; in a container bind `0.0.0.0` **only** behind an authenticated proxy and set `app.require_auth: true`. |
| `allowedHosts` only governs WebSocket hosts | HTTP Host filtering must be added at the reverse proxy when the app is networked. Documented. |
| Escaping would break intentional `<b>` markup in `so_what` | `so_what` keeps trusted markup; only data-derived values are escaped at call sites. |
