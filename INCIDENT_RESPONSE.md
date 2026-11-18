# Incident Response Plan — FlightOps Insight

**Version:** 1.0
**Date:** 2026-11-18
**Scope:** the Streamlit UI (`app.py`), its local data/model artifacts, the optional OIDC
integration, and the CI pipeline. The FastAPI scoring service is covered only where it
shares credentials or artifacts.
**Deployment assumption:** local / internal. This plan is deliberately small; extend it
before any internet-facing deployment.

---

## 1. Roles and contacts

Fill these in. An incident plan with blank contacts is a wish, not a plan.

| Role | Responsibility | Name | Contact |
|---|---|---|---|
| Incident owner | Owns the timeline and decisions | `<name>` | `<phone>` |
| Application owner | Streamlit app, artifacts, deploy | `<name>` | `<email>` |
| Identity admin | OIDC provider, client secrets, MFA, revocations | `<name>` | `<email>` |
| IT / host admin | Workstation or VM, disk, network, backups | `<name>` | `<email>` |
| Security contact | Triage, external reporting | `<name>` | `<email>` |
| Data owner | BTS/C-MAPSS/OpenSky usage questions | `<name>` | `<email>` |

External:

| Party | When | Where |
|---|---|---|
| Identity provider support | Suspected token/client compromise | provider console |
| OpenSky Network | API credential misuse / quota abuse | OpenSky account portal |
| Legal / privacy | Any personal data exposure | `<contact>` |
| Law enforcement | Only on legal advice | — |

---

## 2. Severity

| Level | Definition | Examples | Target response |
|---|---|---|---|
| **SEV-1 Critical** | Confirmed compromise of the host, IdP client secret, or a publicly reachable unauthenticated app | App exposed on `0.0.0.0` without auth; OIDC `client_secret` leaked; malicious model artifact executed | Immediate containment, notify owner within 1 h |
| **SEV-2 High** | Credential exposure without confirmed use, or loss of integrity of artifacts | OpenSky token in a shared log; unexpected commit to `artifacts/`; auth bypass suspected | Contain within 4 h |
| **SEV-3 Medium** | Security control failing but no data impact | CSP regression, tracebacks visible again, agent API enabled unexpectedly | Fix within 2 business days |
| **SEV-4 Low** | Hygiene / informational | Dependabot alert on an unused dependency, failed login noise | Next maintenance window |

When in doubt, start one level higher and downgrade with evidence.

---

## 3. Detection sources

There is **no SIEM and no alerting** today. Detection is manual. Recorded sources:

| Source | Location | What it shows |
|---|---|---|
| Application log | `reports/logs/flightops.log` (5 MB × 3) | auth session established / signed out, provider failures, model-load failures |
| Server stderr/stdout | terminal or service journal | bind address, startup, request errors |
| OIDC provider console | IdP | sign-in events, MFA results, token issuance, anomalous locations |
| OpenSky account | OpenSky portal | quota consumption, token use |
| GitHub | repo → Security / Actions | Dependabot alerts, failed `security` job, secret-scanning alerts |
| Host OS | Windows Event Log / `journalctl` | logins, service changes, firewall events |
| User report | any channel | "the app looks different", phishing page, unexpected login |

**Minimum viable monitoring (do this now):**

```powershell
# Watch auth and error lines
Get-Content reports\logs\flightops.log -Wait -Tail 50 |
  Select-String -Pattern "Authenticated|signed out|ERROR|Authentication required"
```

Review the OIDC provider's sign-in log weekly while the app is shared.

Not available, and must not be claimed: real-time alerting, log shipping, tamper-evident
storage, EDR, network IDS.

---

## 4. Triage flow

```
Report / alert
   │
   ├─ Is the app reachable from outside 127.0.0.1?
   │     → treat as SEV-1 until proven otherwise (§6.1)
   │
   ├─ Is a secret (OIDC client_secret, cookie_secret, OpenSky token) exposed?
   │     → §6.2 rotate first, investigate second
   │
   ├─ Did a file under artifacts/ or data/ change unexpectedly?
   │     → §6.3 preserve then verify
   │
   ├─ Did a dependency or CI workflow change unexpectedly?
   │     → §6.4
   │
   └─ Otherwise → §6.5 (content/XSS) or §6.6 (host loss)
```

**Golden rules**

1. **Do not delete evidence.** Copy logs before rotating or restarting.
2. **Rotate before you understand** when a credential is exposed; understanding can follow.
3. **Do not "test in production"** during an incident. Reproduce on a copy.
4. Record timestamps in UTC and who did what.

---

## 5. Evidence collection (first 15 minutes)

```powershell
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$box   = "reports/incident-$stamp"
New-Item -ItemType Directory -Path $box | Out-Null

Copy-Item reports\logs\flightops.log* $box -ErrorAction SilentlyContinue
Get-Process | Where-Object { $_.ProcessName -match "python|streamlit" } |
  Select-Object Id,ProcessName,StartTime,Path | Out-File "$box\processes.txt"
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 8501,8502,8080 } |
  Out-File "$box\listeners.txt"
git status --porcelain=v1 | Out-File "$box\git-status.txt"
git log -5 --oneline | Out-File "$box\git-log.txt"
Get-ChildItem artifacts,data -Recurse -File |
  Select-Object FullName,Length,LastWriteTime |
  Out-File "$box\file-inventory.txt"
```

Do **not** copy `secrets.toml`, `.env`, tokens or the model binaries into the incident
folder. Record their presence, hashes and timestamps only:

```powershell
Get-FileHash artifacts\models\*.joblib,artifacts\models\*.pt -Algorithm SHA256 |
  Out-File "$box\artifact-hashes.txt"
```

Handle the folder as confidential; remove it after the post-mortem if it contains
anything sensitive.

---

## 6. Playbooks

### 6.1 Unauthorized exposure / unexpected reachability (SEV-1)

**Symptoms:** `Get-NetTCPConnection` shows `0.0.0.0:8501`; a remote machine reaches the
app; the app renders without a login screen on a shared host.

**Immediate containment**

```powershell
# Stop the app
Get-Process python | Where-Object { $_.Path -like "*\.venv\*" } | Stop-Process -Force

# Confirm the hardened bind before restarting
Select-String -Path .streamlit\config.toml -Pattern 'address|allowedHosts|corsAllowedOrigins'
```

Then:

1. Block inbound 8501 at the host firewall.
2. Audit who connected: OS connections, proxy logs if any, IdP sign-ins.
3. If authentication was absent on a shared network, assume **all data displayed was
   read** and that the OpenSky quota was usable. Data is public; quota abuse is the main
   impact.
4. Restart only after `address = "127.0.0.1"`, `app.require_auth: true` and a complete
   `[auth]` block are in place.
5. Check for CLI/env overrides (`--server.address`, `STREAMLIT_SERVER_ADDRESS`).

**Eradication:** remove the override, add the host firewall rule, verify with the probe in
`SECURITY_CONFIGURATION.md` §8.

**Recovery:** re-run `pytest`, the header probe, and one authenticated sign-in.

---

### 6.2 Credential or secret exposure (SEV-1/SEV-2)

Covers `OPENSKY_ACCESS_TOKEN`, `OPENSKY_CLIENT_SECRET`, OIDC `client_secret`, and
`cookie_secret`.

**Rotate first — in this order**

| Secret | Rotation | Side effects |
|---|---|---|
| OIDC `client_secret` | IdP console → new secret → update `secrets.toml` → restart | none if done correctly |
| OIDC `cookie_secret` | new `secrets.token_urlsafe(48)` → restart | **all users signed out** |
| OpenSky token | OpenSky portal → revoke → issue new → update environment | live page falls back to demo snapshot if invalid |
| OpenSky client secret | same as token | same |

```powershell
# cookie secret
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then:

1. Search for where it leaked: `git log -p`, CI logs, ticket attachments, chat exports,
   shell history (`Get-Content (Get-PSReadLineOption).HistorySavePath`).
2. If it reached a git remote, history rewrite is **not** sufficient; rotation is the fix.
3. Review IdP sign-in logs and OpenSky usage for the exposure window.
4. Record the window, the consumers, and whether misuse occurred.
5. If the OIDC client was abused, disable the client in the IdP until rotation completes.

**Do not** paste the new secret into chat, tickets, or the incident folder.

---

### 6.3 Artifact or data tampering (SEV-2)

**Symptoms:** unexpected modification under `artifacts/`, `data/processed/`, or
`config.yaml`; a model produces implausible output; a new `.joblib`/`.pt` appears.

**Why it matters:** `joblib.load` deserialises arbitrary Python objects (M-04). A tampered
artifact is code execution, not just wrong numbers.

**Steps**

1. Stop the app and any scripts.
2. Compare hashes with the recorded inventory (§5) and with git (artifacts are git-ignored,
   so rely on your own manifest or a rebuild).
3. Do **not** load the suspect artifact to "check" it.
4. Rebuild from source in a clean environment:

   ```powershell
   python scripts/run_all.py --force
   ```

5. Compare regenerated metrics to `reports/metrics/*.json` expectations.
6. If the artifact could have been loaded by a running process, treat the host as
   potentially compromised: isolate it, review process/network activity, rotate any secret
   readable by that account (including `secrets.toml`).
7. Add a SHA-256 manifest check before `joblib.load` if this recurs (open item M-04).

---

### 6.4 Dependency or CI compromise (SEV-2)

**Symptoms:** Dependabot/`pip-audit` alarm on the release commit; an unexpected commit to
`.github/workflows/ci.yml`, `requirements.txt` or `pyproject.toml`; a new package appears
in the environment.

**Steps**

1. `git log -p -- .github/workflows requirements.txt pyproject.toml` — identify the change
   and author.
2. If the workflow changed, **disable Actions** for the repo until reviewed (`Settings →
   Actions → Disable`), because a malicious workflow can read `GITHUB_TOKEN`.
3. Reproduce the audit locally:

   ```powershell
   python -m pip_audit -r requirements.txt --progress-spinner off
   python -m bandit -r src deployment -ll -q
   ```

4. Pin the offending dependency to a known-good version, or remove it.
5. Rebuild the virtual environment from scratch; do not trust an environment that
   installed the suspect package.
6. Rotate any secret that was available to the workflow (there should be none — CI only
   needs `contents: read`).

---

### 6.5 Malicious or misleading third-party content (SEV-3)

**Symptoms:** a callsign/country field renders oddly; a link appears in the live-tracking
detail; a user reports a phishing-style overlay.

**Steps**

1. Confirm the sink escaped the value. Escaping is applied in
   `live_tracking.py` (caption + call-out) and `common.py` (`page_header`,
   `assumption_box`). `so_what` accepts trusted markup **by design** — confirm no
   third-party value is passed to it.
2. Capture the raw provider payload (OpenSky) for the offending record if possible.
3. Add the concrete payload as a regression test in `tests/test_security.py`.
4. Report upstream if OpenSky data is abusive; it is a public feed.
5. Re-run `pytest` and reload the page.

Streamlit's own HTML sanitisation is version-dependent; do not rely on it. The escaping
helpers are the control.

---

### 6.6 Host loss, ransomware or disk failure (SEV-1/SEV-2)

**Scope note:** there is no operational database. The valuable, non-reproducible items are
**`secrets.toml`**, **`config.yaml`**, and any local-only artifacts.

**Recovery order**

1. Isolate the host from the network. Do not pay, do not attach unknown drives.
2. Rebuild the OS; do not reuse the compromised install.
3. Restore from backup (§7) **after** scanning it.
4. Reinstall dependencies from `requirements.txt` in a fresh `.venv`.
5. Regenerate data and artifacts if they were not backed up:
   `python scripts/run_all.py --force` (this re-downloads public data; expect time).
6. Recreate `secrets.toml` from the IdP/secret manager, not from an untrusted copy.
7. Verify with the checklist in `SECURITY_CONFIGURATION.md` §9.

---

## 7. Backup and restore

| Item | Reproducible? | Backup approach |
|---|---|---|
| Source code | yes (git) | remote git + this repo |
| `config.yaml` | yes, but reviewed values matter | git |
| `.streamlit/secrets.toml` | **no** | password manager / secret manager; never in git |
| `data/raw`, `data/processed` | yes (public downloads) | optional; re-download |
| `artifacts/` | yes (`scripts/run_all.py`) | optional; rebuild + hash manifest |
| `reports/` | yes | optional |
| Logs | no | retain per policy, not required for recovery |

**Test the restore, don't assume it.** A minimum drill:

1. Copy `secrets.toml` to a scratch host with restricted permissions.
2. Start the app with `FLIGHTOPS_REQUIRE_AUTH=1`.
3. Sign in through the IdP.
4. Confirm the OTP overview renders and one model page loads.
5. Delete the scratch copy and revoke any temporary credential.

Record date, operator and result. Repeat quarterly, or after any credential change.

---

## 8. Communication

**Internal, within the response window**

```
SEV: <1-4>
What: <one sentence>
Impact: <what data/access is affected>
Contained: <yes/no>
Actions taken: <bullets>
Next update: <time UTC>
```

**External notification** — decide with legal/privacy:

- IdP compromise → follow the provider's process and your organisation's policy.
- Personal data exposure → there is no personal data in the prototype dataset, but OIDC
  claims (name/email) exist in memory and may appear in a log. Check before declaring
  "no personal data".
- Public data integrity → mention the rebuild and the affected period.

Do not speculate publicly about cause or scope before the timeline is verified.

---

## 9. Post-incident review

Within 5 business days for SEV-1/2:

| Question | Capture |
|---|---|
| What happened, in UTC timestamps? | timeline |
| How was it detected? | source + delay |
| What was the blast radius? | hosts, secrets, data, quota |
| What worked? | controls that held |
| What failed? | control gaps, with finding IDs |
| Root cause | technical **and** process |
| Actions | owner + due date + verification method |
| New tests | added to `tests/` or docs |

Update `SECURITY_AUDIT.md` and `SECURITY_TEST_RESULTS.md` with anything learned. Do not
quietly close an incident; change the documented posture.

---

## 10. Known limitations of this plan

- No automated alerting; detection depends on someone looking.
- Logs are local and mutable; an attacker with host access can alter them.
- No out-of-hours rota; response times are best-effort.
- No tested forensic tooling; evidence collection is basic and must not be treated as
  court-admissible.
- FastAPI `/predict` and container incidents have no runbook yet because those assets are
  out of scope for this pass.

Fix the first three before any internet-facing deployment.
