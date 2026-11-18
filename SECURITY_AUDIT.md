# Security Audit — FlightOps Insight

| | |
|---|---|
| **Audit date** | 2026-11-18 |
| **Repository** | `D:\project learning` |
| **Baseline commit** | `f278961` plus uncommitted work in the tree |
| **Scope** | Streamlit application, analytics/data layer, ML artifact loading, deployment templates, CI, configuration, git history |
| **Deployment assumption** | Local / internal workstation only (user-confirmed). No public internet exposure. |
| **Status** | Phase 1 audit + Phase 4 app/config hardening complete. Infrastructure controls documented but not deployed. |

> This is a risk assessment of a **decision-support prototype**, not a certification. No system is "100% secure". Controls are marked **Verified**, **Configured (not exercised)**, **Open**, or **N/A**.

---

## 1. Architecture discovered

```
Browser
  │  HTTP + WebSocket (localhost:8501)
  ▼
Streamlit 1.67  ── optional OIDC via st.login (IdP)
  │
  ├─ DuckDB (read-only) ── data/processed/flightops.duckdb
  ├─ Parquet / CSV / JSON artifacts
  ├─ joblib / LightGBM / PyTorch model artifacts
  └─ HTTPS ── OpenSky Network ADS-B API (optional OAuth2)

Separate surface (not run by `app.py`):
  FastAPI /health + /predict  →  Vertex AI custom container
Batch surface:
  scripts/*, Databricks bundle, Vertex AI pipeline skeletons
```

| Layer | Technology | Notes |
|---|---|---|
| UI / web server | Streamlit 1.67 (Uvicorn under the hood) | No custom backend routes |
| Auth | None by default; optional native OIDC | Added in this pass |
| Data | DuckDB read-only, Parquet, CSV/JSON | Public BTS/C-MAPSS + synthetic data |
| ML | LightGBM, logistic regression, SHAP, PyTorch, OR-Tools | Artifacts trusted-local |
| External calls | OpenSky, BTS, GitHub C-MAPSS mirror | Fixed HTTPS hosts, no user-controlled URLs |
| Deployment | Local scripts; Vertex AI / Databricks templates | No TLS proxy or WAF in repo |

**Assets:** model artifacts, analytics outputs, OpenSky OAuth credentials, OIDC client secret and cookie signing key, and (indirectly) the OpenSky API quota.

**Trust boundaries:** browser → Streamlit; Streamlit → local filesystem/DB; Streamlit → OpenSky; FastAPI → model artifacts. There is no multi-tenant data store and no user-owned object, so classic IDOR and privilege-escalation paths do not exist today.

---

## 2. Method

1. Read every application, data, live-tracking, config and deployment source file (92 tracked files; data/artifacts inspected by type/metadata).
2. Reviewed `config.yaml`, `.gitignore`, CI workflow, Dockerfile, Databricks/Vertex templates.
3. Searched tracked text and git history for credential patterns (`AKIA…`, private keys, `password=`, `secret=`, tokens).
4. Ran `pip-audit 2.10.1` against `requirements.txt`.
5. Ran `bandit 1.9.4` (`-ll`) across `src/` and `deployment/`.
6. Ran the 58-test suite and two Streamlit `AppTest` executions.
7. Started the hardened server on `127.0.0.1:8502` and inspected HTTP responses, cookies, CORS and Host handling.
8. Reviewed deployment assumptions with the owner; scoped controls to a local/internal deployment with optional OIDC.

Not performed (and why): OWASP ZAP (no proxy/TLS target), TLS/HSTS/CSP verification (no TLS terminator in scope), live OIDC login (no IdP credentials), container build/scan (deployment assets out of scope), destructive testing (none).

---

## 3. Findings

Severity is stated for the **current local-only posture** and, in brackets, for a hypothetical **internet-facing** deployment.

### 3.1 High / Critical

| ID | Finding | Local | Public | Status |
|---|---|---|---|---|
| **H-01** | No authentication or authorization; anyone reaching the port gets full access | Low | **Critical** | **Mitigated, opt-in.** Native OIDC gate added; disabled by default for local use. `FLIGHTOPS_REQUIRE_AUTH=1` fails closed. |
| **H-02** | Streamlit renders full Python tracebacks, paths and exception messages in the browser by default | Medium | **High** | **Fixed.** `client.showErrorDetails="none"`; details still logged server-side. |
| **H-03** | FastAPI `/predict` has no auth, rate limit or body-size limit | N/A | **High** | **Open (out of scope).** Service is not part of the local app. |
| **H-04** | FastAPI returns `detail=str(exc)` to the caller | N/A | **High** | **Open (out of scope).** |
| **H-05** | Container runs as root; `COPY . /app`; no `.dockerignore` | N/A | **High** | **Open (deployment asset, out of scope).** |
| **H-06** | No HTTPS, HSTS, CSP or HTTP security headers | Low (loopback HTTP) | **High** | **Open for networked use.** Template and checklist documented; not applicable to loopback-only HTTP. |
| **H-07** | No rate limiting on OpenSky calls; user can trigger Global queries | Low | **Medium-High** | **Partially mitigated.** Fetch is server-side, 30 s cache, 20 s timeout, demo fallback, display cap. No per-user quota. |

### 3.2 Medium

| ID | Finding | Status |
|---|---|---|
| **M-01** | Third-party ADS-B values interpolated into `unsafe_allow_html` and Markdown | **Fixed** for live-tracking HTML and detail caption: `escape_html` / `escape_markdown`. Other pages use repository-authored text. |
| **M-02** | No dependency/secret/static-analysis scanning; broad version ranges | **Partially fixed.** `pip-audit`, `bandit` (reviewed inline suppressions), Dependabot added. Version ranges and lockfile remain open. |
| **M-03** | Downloaded BTS/C-MAPSS files are not checksum-verified | **Open.** HTTPS only. |
| **M-04** | `joblib.load` of model artifacts without integrity verification | **Accepted (local trusted artifacts).** `torch.load` already uses `weights_only=True`. |
| **M-05** | Streamlit CORS/allowed-host defaults not pinned | **Partially fixed.** Explicit origin/host allowlists; cross-origin WebSocket handshakes for foreign and `null` origins are rejected (verified). `allowedHosts` does **not** filter the static index route — a foreign `Host` still gets `200` — so HTTP Host filtering must be done at a proxy. |
| **M-06** | No security-event logging or alerting | **Partially fixed.** Auth session and provider failures are logged without PII. No SIEM/alerting. |
| **M-07** | Internal exception text shown in UI | **Fixed** in predictor and live-tracking paths. |

### 3.3 Low

| ID | Finding | Status |
|---|---|---|
| L-01 | Data/DB unencrypted at rest; no backup procedure | **Accepted.** Public/rebuildable data; use OS disk encryption for shared hosts. |
| L-02 | `FLIGHTOPS_CONFIG` / `FLIGHTOPS_ROOT` drive service config/module paths | **Accepted.** Set by the operator, not the request. |
| L-03 | FastAPI `flight_date` is a free string → 500 instead of 422 | **Open (out of scope).** |
| L-04 | GitHub Actions pinned to major tags, not commit SHAs | **Open.** Dependabot updates actions monthly. |
| L-05 | No explicit Uvicorn/proxy header/body limits | **Open** for the FastAPI service; Streamlit message size is capped at 50 MB. |
| L-06 | Streamlit 1.67 registers experimental agent API routes (`/_stcore/agent/interact`, `/mcp`, `/openapi.json`); off by default but enable-able by config | **Mitigated.** `server.enableAgentApi = false` pinned; runtime probe returns `403 not_available`, schema reports `disabled`. |
| L-07 | `server.trustedUserHeaders` would make `st.user` trust client-supplied headers if populated | **Mitigated.** Pinned to empty `{}`; documented as proxy-only. |

### 3.4 Positive controls already present

- No credentials in tracked files or git history (spot-checked).
- `.gitignore` covers `.env*`, `secrets.toml`, `*.pem`, `*.parquet`, `*.duckdb`, `*.joblib`, `*.pt`, logs.
- User filters use bound `?` parameters; DuckDB is read-only.
- Zip extraction streams a single member (no `extractall`, no Zip-Slip).
- `torch.load(..., weights_only=True)`.
- Outbound hosts are fixed and HTTPS with verification enabled.
- Rotating logs (5 MB × 3); no passwords, tokens or PII logged.
- CI has `permissions: contents: read`.
- Experimental Streamlit agent API is disabled and pinned off; `trustedUserHeaders` is empty.
- Cross-origin WebSocket handshakes are rejected for foreign and `null` origins (verified).

---

## 4. Controls from the original brief that are N/A

| Requested control | Why N/A |
|---|---|
| Argon2id password hashing, password reset, MFA, lockout, session fixation | No local accounts. OIDC delegates these to the IdP. |
| RBAC, privilege escalation, IDOR | No roles or user-owned objects; read-only decision support. |
| File-upload validation | No upload feature. |
| CSRF tokens | Streamlit XSRF protection is enabled; no state-changing API routes. |
| SQL injection in request paths | SQL is repository-authored and parameterized; no request data reaches SQL text. |
| SSRF | No user-supplied URL is fetched. |
| Database migrations/backups | No operational database; public datasets are reproducible. |
| WAF / DDoS | Infrastructure-level; must be provided by a proxy or cloud provider. |

---

## 5. Residual risk statement

For **localhost-only** use, the residual risk is low: the main remaining issues are local process integrity and dependency drift. For **any shared or internet-facing** deployment, H-01 (unless OIDC is enabled), H-03/H-04, H-05 and H-06 must be closed first. The FastAPI service and container assets were reviewed but deliberately not modified in this pass.

See `SECURITY_IMPLEMENTATION.md` for changes, `SECURITY_TEST_RESULTS.md` for evidence, `SECURITY_CONFIGURATION.md` for setup, and `INCIDENT_RESPONSE.md` for response procedures.
