"""Tests for the small security helpers and the auth fail-closed switch."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from flightops.analysis.otp_insights import _SEGMENT_DIMENSIONS, _segment_query
from flightops.app import auth
from flightops.security import escape_html, escape_markdown, mask_email

CONFIG_TOML = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"


def test_escape_html_neutralises_script_and_quotes() -> None:
    payload = '<script>alert("x")</script>'
    escaped = escape_html(payload)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
    assert '"' not in escaped


def test_escape_html_handles_none() -> None:
    assert escape_html(None) == ""


def test_escape_markdown_neutralises_link_injection() -> None:
    payload = "[click](https://evil.example)"
    escaped = escape_markdown(payload)
    assert escaped == r"\[click\]\(https://evil\.example\)"
    assert "](" not in escaped


def test_escape_markdown_strips_control_characters() -> None:
    assert escape_markdown("AIC\x00123") == "AIC123"


def test_mask_email_never_returns_the_full_address() -> None:
    masked = mask_email("analyst@example.com")
    assert masked == "a***@example.com"
    assert "analyst" not in masked


def test_mask_email_handles_invalid_values() -> None:
    assert mask_email(None) == "signed-in user"
    assert mask_email("") == "signed-in user"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_requires_auth_truthy(value: str) -> None:
    assert auth.env_requires_auth(value) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", None])
def test_env_requires_auth_falsey(value: str | None) -> None:
    assert auth.env_requires_auth(value) is False


def test_segment_dimension_allowlist_is_explicit() -> None:
    assert "carrier" in _SEGMENT_DIMENSIONS
    assert "dep_hour" in _SEGMENT_DIMENSIONS


@pytest.mark.parametrize(
    "hostile",
    [
        "carrier; DROP TABLE flights",
        "carrier) --",
        "unknown_column",
        "1; SELECT * FROM information_schema.tables",
    ],
)
def test_segment_query_rejects_non_allowlisted_dimension(hostile: str) -> None:
    # Rejected before any SQL is built or executed; cfg is never touched.
    with pytest.raises(ValueError):
        _segment_query(None, hostile)  # type: ignore[arg-type]


def test_streamlit_config_keeps_hardened_defaults() -> None:
    config = tomllib.loads(CONFIG_TOML.read_text(encoding="utf-8"))

    assert config["server"]["address"] == "127.0.0.1"
    assert config["server"]["enableCORS"] is True
    assert config["server"]["enableXsrfProtection"] is True
    assert config["server"]["enableStaticServing"] is False
    assert config["server"]["maxUploadSize"] <= 10
    assert set(config["server"]["allowedHosts"]) == {"localhost", "127.0.0.1"}
    assert config["server"]["enableAgentApi"] is False
    assert config["server"]["trustedUserHeaders"] == {}
    assert config["client"]["showErrorDetails"] == "none"
    assert config["browser"]["gatherUsageStats"] is False


def test_streamlit_parser_accepts_hardened_config() -> None:
    """The file must be valid for Streamlit itself, not only for tomllib."""
    from streamlit import config as streamlit_config

    assert streamlit_config.get_option("server.address") == "127.0.0.1"
    assert streamlit_config.get_option("server.enableAgentApi") is False
    assert streamlit_config.get_option("server.enableStaticServing") is False
    assert streamlit_config.get_option("server.enableXsrfProtection") is True
    assert streamlit_config.get_option("server.enableCORS") is True
    assert streamlit_config.get_option("client.showErrorDetails") == "none"
    assert streamlit_config.get_option("browser.gatherUsageStats") is False
