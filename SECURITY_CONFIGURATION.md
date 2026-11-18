# Security Configuration — FlightOps Insight

**Applies to:** Streamlit 1.67 UI at `app.py`
**Current deployment:** local / internal workstation, loopback HTTP, optional OIDC
**Last updated:** 2026-11-18

This document describes the security-relevant configuration that exists **in the
repository now**, and gives a reference design for the proxy/TLS layer that is
**not** deployed. Anything not marked *active* is a template.

---

## 1. Deployment modes

| Mode | Bind | Auth | TLS | Status |
|---|---|---|---|---|
| **A. Workstation (current)** | `127.0.0.1` | optional OIDC | none (loopback) | ✅ active, tested |
| **B. Internal LAN** | `127.0.0.1` behind proxy | **OIDC required** | required | ⚠️ needs proxy work |
| **C. Internet-facing** | behind proxy/CDN | OIDC + network policy | required | ❌ not ready |

Mode A is what the hardening targets. Modes B and C need the proxy design in §7 plus the
open items in `SECURITY_AUDIT.md` (H-03 … H-06).

---

## 2. Active configuration reference

### 2.1 `.streamlit/config.toml`

This file is committed intentionally: it contains no secrets and it pins security
defaults so an upgrade cannot silently loosen them.

| Key | Value | Why |
|---|---|---|
| `server.address` | `127.0.0.1` | Only reachable from the local machine |
| `server.port` | `8501` | Default; override with `--server.port` when needed |
| `server.headless` | `true` | No browser auto-open; predictable service start |
| `server.enableCORS` | `true` | Required for the WebSocket origin check |
| `server.corsAllowedOrigins` | `http://localhost:8501`, `http://127.0.0.1:8501` | Explicit cross-origin allowlist |
| `server.enableXsrfProtection` | `true` | XSRF cookie on state-changing requests |
| `server.allowedHosts` | `localhost`, `127.0.0.1` | Host validation for WebSocket and agent API |
| `server.enableStaticServing` | `false` | No `./static` exposure |
| `server.maxUploadSize` | `5` (MB) | No upload feature; keep the ceiling small |
| `server.maxMessageSize` | `50` (MB) | Bound WebSocket frames |
| `server.enableAgentApi` | `false` | **Pins off** the experimental HTTP/MCP interaction API |
| `server.trustedUserHeaders` | `{}` | **Never** trust client-supplied identity headers |
| `server.maxWidgetStateSize` | `25` (MB) | Bound widget-state request bodies |
| `browser.gatherUsageStats` | `false` | No telemetry to Streamlit |
| `client.showErrorDetails` | `"none"` | No tracebacks/paths in the browser |
| `client.toolbarMode` | `"minimal"` | Hides developer toolbar affordances |
| `logger.level` | `"info"` | Application-level logging |

### 2.2 Verified effective values

```powershell
.\.venv\Scripts\python.exe -c "from streamlit import config; print(config.get_option('server.address'), config.get_option('server.enableAgentApi'), config.get_option('client.showErrorDetails'))"
```

Observed: `127.0.0.1 False none`

### 2.3 Precedence — read this before overriding

Streamlit resolves options in this order (later wins):

1. built-in defaults
2. `~/.streamlit/config.toml` (global)
3. `<project>/.streamlit/config.toml` (this file)
4. environment variables such as `STREAMLIT_SERVER_ADDRESS`
5. CLI flags (`streamlit run app.py --server.address 0.0.0.0`)

**Consequence:** a CLI flag or environment variable can undo the hardened file. Never
launch with `--server.address 0.0.0.0` on an untrusted network, and treat
`STREAMLIT_*` variables as security-relevant change-controlled settings.

Also note a *global* `~/.streamlit/config.toml` could override nothing (it is lower
precedence) but **can** supply options this file does not set. Do not put identity or
network settings there.

### 2.4 `config.yaml` — application switch

```yaml
app:
  require_auth: false
```

| Value | Behaviour |
|---|---|
| `false` (default) | Login gate only when a complete `[auth]` block exists |
| `true` | **Fail closed**: if no provider is configured, no page renders |

Set to `true` for modes B and C.

### 2.5 Environment variables

| Variable | Purpose | Secret? |
|---|---|---|
| `FLIGHTOPS_REQUIRE_AUTH` | `1/true/yes/on` forces fail-closed mode (equivalent to `app.require_auth: true`) | No |
| `FLIGHTOPS_CONFIG` | Alternate config path used by the FastAPI service | No |
| `FLIGHTOPS_ROOT` | Project root used by the FastAPI service | No |
| `OPENSKY_ACCESS_TOKEN` | OpenSky OAuth2 bearer token (raises rate limit) | **Yes** |
| `OPENSKY_CLIENT_ID` | OpenSky OAuth2 client id | No |
| `OPENSKY_CLIENT_SECRET` | OpenSky OAuth2 client secret | **Yes** |
| `MODEL_VERSION`, `GCP_*`, `PIPELINE_ROOT`, `RAW_DATA_URI`, `PROCESSED_DATA_URI` | Vertex AI template values (`deployment/vertex_ai/env.example`) | Mixed |

Never place secrets in `config.yaml`, in shell history, or in CI logs. On Windows, use
the user/machine environment store or a secret manager; on Linux, an `EnvironmentFile`
with `0600` permissions owned by the service account.

---

## 3. Secrets handling

| File | Status |
|---|---|
| `.streamlit/secrets.toml` | **git-ignored** (`.gitignore` line `*.streamlit/secrets.toml`) |
| `.streamlit/secrets.toml.example` | committed template, **no values** |
| `.env`, `.env.*` | git-ignored |
| `*.pem`, keys | git-ignored |

Rules:

1. Create the real file only on the host that runs the app.
2. Restrict permissions: on Linux `chmod 600`, owner = service account; on Windows
   remove inheritance and grant only the service user.
3. Rotate `cookie_secret` and the OIDC `client_secret` on any suspected exposure.
4. Never paste the file into chat, tickets, screenshots or CI.
5. Back it up through the same protected channel as other credentials (see
   `INCIDENT_RESPONSE.md` §7).

Generate values:

```powershell
# cookie signing key
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 4. OIDC setup (optional, recommended for any shared deployment)

### 4.1 Prerequisites

```powershell
python -m pip install -r requirements.txt   # installs Authlib
python -c "import authlib, streamlit; print(authlib.__version__, streamlit.__version__)"
```

### 4.2 Create `.streamlit/secrets.toml`

Minimum viable block — every key is required for the gate to activate:

```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "<output of secrets.token_urlsafe(48)>"
client_id = "<from IdP>"
client_secret = "<from IdP>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
client_kwargs = { prompt = "select_account" }
```

* `redirect_uri` **must** end in `/oauth2callback` (Streamlit validates this) and must
  match the IdP registration **exactly**.
* `cookie_secret` must be stable across restarts; changing it signs everyone out.
* Leave `expose_tokens` unset. The app never calls a downstream API on the user's behalf.

### 4.3 Provider values

| Provider | `server_metadata_url` | Notes |
|---|---|---|
| Google | `https://accounts.google.com/.well-known/openid-configuration` | Allows `http://localhost` redirect URIs for loopback testing |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration` | Register a *Web* platform redirect URI; `http://localhost` allowed for dev |
| Okta | `https://<your-subdomain>.okta.com/.well-known/openid-configuration` | Create an OIDC **Web** app, authorization-code flow |
| Keycloak | `https://<host>/realms/<realm>/.well-known/openid-configuration` | Self-hosted option |

Requested scopes default to `openid profile email`; keep them minimal.

### 4.4 Enable and verify

```powershell
# optional belt-and-braces: fail closed even if the secrets file is deleted
$env:FLIGHTOPS_REQUIRE_AUTH = "1"
python -m streamlit run app.py
```

Expected behaviour:

| State | Result |
|---|---|
| No `[auth]`, switch off | App renders (prototype mode) |
| No `[auth]`, switch on | Error page: *"Authentication is required … Add an [auth] section"*; nothing renders |
| `[auth]` complete, signed out | "Sign in" screen; no page renders |
| `[auth]` complete, signed in | App renders; sidebar shows identity + "Sign out" |

Verification already performed: rows 2–3 of the last two (fail-closed and default) are
covered in `SECURITY_TEST_RESULTS.md` §7. The signed-in row is **not** yet verified
end-to-end; do it once with a real IdP and record the result.

### 4.5 What OIDC gives you — and what it does not

**Gives:** authentication, IdP-managed MFA, conditional access, token signing/validation,
signed session cookie, central deprovisioning.

**Does not give:** per-page authorization (no roles), audit trail inside the app, or
protection of the loopback port from local users. All signed-in users see all pages —
acceptable because every page is read-only analytics.

---

## 5. Data and model configuration

| Item | Setting | Note |
|---|---|---|
| DuckDB | opened `read_only=True` | `data/processed/flightops.duckdb` |
| Backend selection | `database.backend: "duckdb"` | `sqlite` is an unrelated in-progress feature; not part of this hardening |
| Model artifacts | local `artifacts/` | Trusted-local assumption (M-04) |
| Remote data | BTS zip, C-MAPSS mirror, OpenSky | Fixed HTTPS hosts |
| OpenSky cache | 30 s server-side | Limits quota use; not a per-user quota (H-07) |

Do not point `paths.*` at world-writable or network-shared directories unless artifact
integrity is added first (M-04).

---

## 6. Logging configuration

| Property | Value |
|---|---|
| Location | `reports/logs/flightops.log` |
| Rotation | 5 MB × 3 files |
| Contains | auth session established/signed-out events, provider failures, model-load failures |
| Must not contain | passwords, tokens, cookies, full email addresses, raw request bodies |

Auth events are intentionally coarse (no email/subject). If you need per-user audit later,
log an IdP subject identifier — never the email — and add retention policy.

---

## 7. Reference reverse proxy (NOT deployed)

Use this only when moving to mode B/C. It is a starting point, not a tested artifact.

### 7.1 nginx

```nginx
# /etc/nginx/conf.d/flightops.conf
limit_req_zone $binary_remote_addr zone=flightops:10m rate=10r/s;

server {
    listen 80;
    server_name flightops.internal.example;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name flightops.internal.example;

    ssl_certificate     /etc/letsencrypt/live/flightops.internal.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/flightops.internal.example/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_tickets off;

    # Enable only after HTTPS is confirmed working on the real hostname.
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;
    add_header X-Frame-Options "SAMEORIGIN" always;

    # Report-only first. Promote to Content-Security-Policy only after a browser walk
    # of all six pages shows no violations.
    add_header Content-Security-Policy-Report-Only "
        default-src 'self';
        script-src 'self' 'unsafe-inline' 'unsafe-eval';
        style-src 'self' 'unsafe-inline';
        img-src 'self' data: blob: https:;
        font-src 'self' data:;
        connect-src 'self' wss://flightops.internal.example https:;
        frame-ancestors 'self';
        base-uri 'self';
        form-action 'self';
        object-src 'none'" always;

    client_max_body_size 6m;

    # Host allowlist at the edge (Streamlit does not filter the index route).
    if ($host !~* ^(flightops\.internal\.example)$) {
        return 444;
    }

    location / {
        limit_req zone=flightops burst=20 nodelay;

        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket upgrade for Streamlit's /_stcore/stream
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

### 7.2 Changes the proxy forces on the app

| Change | Why |
|---|---|
| `server.address = "127.0.0.1"` stays | Proxy connects over loopback; do not expose the app port |
| `corsAllowedOrigins` must include `https://flightops.internal.example` | WebSocket origin check |
| `allowedHosts` must include the public hostname | WebSocket Host check |
| `app.require_auth: true` | Never rely on network position alone |
| `redirect_uri` becomes `https://flightops.internal.example/oauth2callback` | IdP must be updated too |

### 7.3 Headers — expected effect

| Header | Effect | Risk if wrong |
|---|---|---|
| HSTS | Forces HTTPS after first visit | Bricking on a plain-HTTP host; never set on IP/localhost |
| CSP | Blocks injected script/iframe | Breaks Streamlit/Plotly/map tiles if too strict |
| `X-Frame-Options` / `frame-ancestors` | Blocks framing/clickjacking | Conflict if embedding is ever needed |
| `Permissions-Policy` | Disables unused browser APIs | Safe deny-list |
| `nosniff` | Stops MIME confusion | Safe |

---

## 8. Verification commands

```powershell
# Config is valid for Streamlit and holds the hardened values
.\.venv\Scripts\python.exe -m pytest tests/test_security.py -q

# Effective options
.\.venv\Scripts\python.exe -c "from streamlit import config; print(config.get_option('server.address'), config.get_option('server.enableAgentApi'), config.get_option('client.showErrorDetails'))"

# Binding + headers
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8501/_stcore/health
curl.exe -s -D - -o NUL http://127.0.0.1:8501/

# Agent API must be off
curl.exe -s -o NUL -w "%{http_code}`n" -X POST -H "Content-Type: application/json" -d "{}" http://127.0.0.1:8501/_stcore/agent/interact
# expected: 403
```

After adding the proxy:

```bash
curl -sI https://flightops.internal.example | grep -Ei 'strict-transport|content-security|x-content-type|referrer-policy|permissions-policy'
curl -s -o /dev/null -w '%{http_code}\n' http://flightops.internal.example   # expect 301
openssl s_client -connect flightops.internal.example:443 -tls1_1 </dev/null 2>&1 | grep -i 'handshake failure'  # expect failure
```

---

## 9. Production checklist

Tick every item before exposing the app beyond loopback.

| # | Item | Status |
|---|---|---|
| 1 | `app.require_auth: true` **and** complete `[auth]` block | ☐ |
| 2 | OIDC round trip verified with the real IdP (sign in, refresh, sign out) | ☐ |
| 3 | Reverse proxy terminates TLS (`TLSv1.2`+) and redirects HTTP → HTTPS | ☐ |
| 4 | HSTS enabled **after** confirming HTTPS on the real hostname | ☐ |
| 5 | CSP deployed report-only, browser walk of all six pages, then enforced | ☐ |
| 6 | `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `frame-ancestors` present | ☐ |
| 7 | `server.address` remains `127.0.0.1`; app port not reachable externally | ☐ |
| 8 | `corsAllowedOrigins` and `allowedHosts` updated for the public hostname | ☐ |
| 9 | Edge `Host` allowlist in place (Streamlit does not filter the index route) | ☐ |
| 10 | Rate limiting / connection limits at the proxy | ☐ |
| 11 | `secrets.toml` permissions restricted and backed up | ☐ |
| 12 | `client.showErrorDetails` still `none`; tracebacks only in logs | ☐ |
| 13 | Agent API still disabled | ☐ |
| 14 | `pip-audit` and `bandit` clean in CI on the release commit | ☐ |
| 15 | Log rotation and retention configured; no PII/tokens in logs | ☐ |
| 16 | Backup + restore test for `secrets.toml`, `config.yaml`, artifacts | ☐ |
| 17 | Incident contacts filled in `INCIDENT_RESPONSE.md` | ☐ |
| 18 | OWASP ZAP baseline scan recorded against the proxied host | ☐ |
| 19 | FastAPI `/predict` hardened or kept private (H-03/H-04) | ☐ |
| 20 | Container rebuilt non-root with `.dockerignore` (H-05) | ☐ |

---

## 10. Change control

Treat these as security-relevant and review them like code:

- `.streamlit/config.toml`, `.streamlit/secrets.toml` (shape, not values)
- `config.yaml` → `app.require_auth`, `paths.*`, `database.*`
- `src/flightops/app/auth.py`, `src/flightops/security.py`
- `requirements.txt`, `pyproject.toml`, `.github/workflows/ci.yml`
- reverse-proxy config and TLS settings

After any change, re-run `pytest`, `bandit`, `pip-audit`, and the header/binding
probes in §8.
