"""Small, dependency-free security helpers used by the app and the tests.

The Streamlit app renders a few strings with ``unsafe_allow_html=True`` and a
few more as Markdown.  Values that originate outside the repository (for
example an ADS-B ``origin_country`` or ``callsign``) must never be interpolated
into those sinks verbatim.  The helpers here keep escaping rules in one place so
call sites cannot silently forget them.
"""

from __future__ import annotations

import html
import re
from typing import Any

# Characters that change Markdown structure (links, emphasis, code, headings).
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_html(value: Any) -> str:
    """Escape a value for interpolation into an HTML fragment.

    ``quote=True`` also escapes quotes so the result is safe inside an
    attribute if a future template needs one.
    """
    return html.escape("" if value is None else str(value), quote=True)


def escape_markdown(value: Any) -> str:
    """Escape a value so it renders as literal text in Streamlit Markdown.

    Streamlit's Markdown renderer still understands links, emphasis and code
    even when ``unsafe_allow_html`` is false, so third-party strings are
    neutralised before display.
    """
    text = "" if value is None else str(value)
    text = _CONTROL_CHARS.sub("", text)
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)


def mask_email(email: Any) -> str:
    """Return a non-reversible display form of an email address."""
    text = "" if email is None else str(email).strip()
    if "@" not in text:
        return "signed-in user"
    local, _, domain = text.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"
