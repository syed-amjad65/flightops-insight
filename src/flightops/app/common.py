"""Shared Streamlit utilities: configuration, styling and decision boxes."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from flightops.config import Config, load_config


@st.cache_resource
def get_config() -> Config:
    return load_config()


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1400px;}
        h1, h2, h3 {letter-spacing: -0.02em;}
        .subtle {color: #6b7280; font-size: 0.92rem;}
        .so-what {
            background: linear-gradient(90deg, #0f172a 0%, #1e3a5f 100%);
            color: white; padding: 1rem 1.2rem; border-radius: 0.65rem;
            border-left: 6px solid #38bdf8; margin-top: 1.2rem; margin-bottom: 0.5rem;
            font-size: 0.98rem; line-height: 1.45;
        }
        .so-what b {color: #7dd3fc;}
        .assumption {
            background: #fff7ed; border: 1px solid #fdba74; color: #7c2d12;
            padding: 0.75rem 1rem; border-radius: 0.5rem; font-size: 0.86rem;
        }
        .risk-critical {color: #b91c1c; font-weight: 700;}
        .risk-high {color: #c2410c; font-weight: 700;}
        .risk-watch {color: #a16207; font-weight: 600;}
        div[data-testid="stMetricValue"] {font-size: 1.55rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.markdown(f'<div class="subtle">{subtitle}</div>', unsafe_allow_html=True)
    st.write("")


def so_what(text: str) -> None:
    st.markdown(
        f'<div class="so-what"><b>So what / recommended action</b><br>{text}</div>',
        unsafe_allow_html=True,
    )


def assumption_box(text: str) -> None:
    st.markdown(f'<div class="assumption">{text}</div>', unsafe_allow_html=True)


def money(value: float, digits: int = 0) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.{digits}f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.0f}k"
    return f"${value:,.0f}"


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:,.{digits}f}%"


def show_missing_artifact(what: str, hint: str) -> None:
    st.warning(f"{what} is not available yet.")
    st.code(hint, language="powershell")
