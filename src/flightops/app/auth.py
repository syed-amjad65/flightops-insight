"""Optional OIDC authentication gate for the Streamlit app.

Authentication is **opt-in** so a local prototype keeps working without an
identity provider:

* When ``.streamlit/secrets.toml`` contains a complete ``[auth]`` section,
  every page requires a signed-in user.
* When no provider is configured the app renders as before, unless
  ``FLIGHTOPS_REQUIRE_AUTH=1`` (or ``app.require_auth: true`` in
  ``config.yaml``) is set, in which case the app fails closed.

Streamlit delegates the OIDC flow (state, nonce, token validation and the
signed session cookie) to ``st.login``/``st.user``.  No password, MFA or
session logic is implemented here, which avoids re-inventing a credential
store.  ``Authlib`` must be installed for ``st.login`` to work.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

import streamlit as st

from flightops.logging_utils import get_logger
from flightops.security import mask_email

logger = get_logger("app.auth")

REQUIRE_AUTH_ENV = "FLIGHTOPS_REQUIRE_AUTH"
_TRUTHY = {"1", "true", "yes", "on"}
_REQUIRED_AUTH_KEYS = ("client_id", "client_secret", "server_metadata_url", "redirect_uri", "cookie_secret")


def env_requires_auth(value: str | None = None) -> bool:
    """Pure helper so the fail-closed switch can be unit tested."""
    raw = os.environ.get(REQUIRE_AUTH_ENV, "") if value is None else value
    return str(raw).strip().lower() in _TRUTHY


def requires_auth(cfg: Any = None) -> bool:
    if env_requires_auth():
        return True
    if cfg is not None:
        try:
            return bool(cfg.get("app.require_auth", False))
        except Exception:  # pragma: no cover - defensive
            return False
    return False


def auth_configured() -> bool:
    """True when a complete ``[auth]`` provider is present in secrets.toml."""
    try:
        section = st.secrets.get("auth")
    except Exception:  # secrets.toml missing or unreadable
        return False
    if not section:
        return False
    try:
        return all(bool(section.get(key)) for key in _REQUIRED_AUTH_KEYS)
    except Exception:  # pragma: no cover - defensive
        return False


def authlib_available() -> bool:
    return importlib.util.find_spec("authlib") is not None


def current_user() -> dict[str, Any]:
    """Return the current identity claims, or an empty mapping."""
    try:
        return dict(st.user.to_dict())
    except Exception:  # no auth configured, or no script run context
        return {}


def _render_login() -> None:
    st.title("FlightOps Insight")
    st.caption(
        "Access is restricted to approved users. Sign in with your organisation "
        "account to continue."
    )
    if st.button("Sign in", type="primary", width="stretch"):
        if not authlib_available():
            st.error(
                "OIDC support is not installed on this host. Install it with "
                "`pip install \"streamlit[auth]\"` and restart the app."
            )
            logger.error("OIDC login attempted without Authlib installed")
            st.stop()
        st.login()


def require_user(cfg: Any = None) -> dict[str, Any]:
    """Enforce authentication when configured, then return the user claims."""
    configured = auth_configured()
    user = current_user() if configured else {}

    if configured and user.get("is_logged_in"):
        if not st.session_state.get("auth_event_logged"):
            st.session_state["auth_event_logged"] = True
            logger.info("Authenticated OIDC session established")
        return user

    if configured:
        _render_login()
        st.stop()

    if requires_auth(cfg):
        st.error(
            "Authentication is required for this deployment, but no OIDC provider "
            "is configured. Add an [auth] section to .streamlit/secrets.toml."
        )
        logger.error("Authentication required but no OIDC provider is configured")
        st.stop()

    return {}


def render_identity() -> None:
    """Sidebar footer showing who is signed in, with an explicit log-out."""
    user = current_user()
    if not user.get("is_logged_in"):
        return
    display = user.get("name") or mask_email(user.get("email")) or "signed-in user"
    with st.sidebar:
        st.caption(f"Signed in as {display}")
        if st.button("Sign out", width="stretch"):
            logger.info("Authenticated user signed out")
            st.logout()
