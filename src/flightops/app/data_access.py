"""Cached data access for the Streamlit decision-support app."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from flightops.config import Config
from flightops.data.database import query_df, query_named


@st.cache_data(ttl=600)
def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@st.cache_data(ttl=600)
def _read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


@st.cache_data(ttl=600)
def _query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return query_df(_config(), sql, list(params))


@st.cache_resource
def _config() -> Config:
    from flightops.app.common import get_config

    return get_config()


def otp_metrics(cfg: Config) -> dict:
    path = cfg.path("paths.metrics") / "otp_metrics.json"
    return _read_json(str(path)) if path.exists() else {}


def otp_insights(cfg: Config) -> dict:
    path = cfg.path("paths.metrics") / "otp_insights.json"
    return _read_json(str(path)) if path.exists() else {}


def rul_metrics(cfg: Config) -> dict:
    path = cfg.path("paths.metrics") / "rul_metrics.json"
    return _read_json(str(path)) if path.exists() else {}


def watchlist(cfg: Config) -> pd.DataFrame:
    path = cfg.path("paths.metrics") / "rul_watchlist.csv"
    return _read_csv(str(path)) if path.exists() else pd.DataFrame()


def alert_costs(cfg: Config) -> pd.DataFrame:
    path = cfg.path("paths.metrics") / "rul_alert_cost.csv"
    return _read_csv(str(path)) if path.exists() else pd.DataFrame()


def scenario_results(cfg: Config) -> dict:
    path = cfg.path("paths.optimization") / "scenario_results.json"
    return _read_json(str(path)) if path.exists() else {}


def maintenance_plan(cfg: Config, scenario: str = "optimized") -> pd.DataFrame:
    path = cfg.path("paths.optimization") / f"maintenance_plan_{scenario}.csv"
    return _read_csv(str(path)) if path.exists() else pd.DataFrame()


def instance_aircraft(cfg: Config) -> pd.DataFrame:
    path = cfg.path("paths.optimization") / "instance_aircraft.csv"
    return _read_csv(str(path)) if path.exists() else pd.DataFrame()


def sensitivity(cfg: Config) -> pd.DataFrame:
    path = cfg.path("paths.optimization") / "sensitivity_aog_penalty.csv"
    return _read_csv(str(path)) if path.exists() else pd.DataFrame()


def named_query(name: str) -> pd.DataFrame:
    return query_named(_config(), name)


def filtered_otp(cfg: Config, carriers: list[str], months: list[int]) -> dict:
    """Aggregate KPIs for the interactive filters."""
    carrier_clause = "1=1"
    params: list = []
    if carriers:
        placeholders = ",".join(["?"] * len(carriers))
        carrier_clause = f"carrier IN ({placeholders})"
        params.extend(carriers)
    month_clause = "1=1"
    if months:
        placeholders = ",".join(["?"] * len(months))
        month_clause = f"month IN ({placeholders})"
        params.extend(months)
    base = f"FROM flights WHERE {carrier_clause} AND {month_clause}"
    overview = query_df(
        cfg,
        f"""SELECT COUNT(*) flights,
                   ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END),4) delay_rate,
                   ROUND(AVG(dep_delay_min),2) avg_dep_delay_min,
                   ROUND(AVG(cancelled),4) cancellation_rate
            {base}""",
        params,
    ).iloc[0].to_dict()
    monthly = query_df(
        cfg,
        f"""SELECT month, COUNT(*) flights,
                   ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END),4) delay_rate,
                   ROUND(1 - AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END),4) otp_rate
            {base} GROUP BY 1 ORDER BY 1""",
        params,
    )
    hourly = query_df(
        cfg,
        f"""SELECT dep_hour, COUNT(*) flights,
                   ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END),4) delay_rate,
                   ROUND(AVG(prev_arr_delay_obs),1) inbound_delay
            {base} GROUP BY 1 ORDER BY 1""",
        params,
    )
    carriers_df = query_df(
        cfg,
        f"""SELECT carrier, COUNT(*) flights,
                   ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END),4) delay_rate
            {base} GROUP BY 1 HAVING COUNT(*) >= 1000 ORDER BY delay_rate DESC""",
        params,
    )
    return {"overview": overview, "monthly": monthly, "hourly": hourly, "carriers": carriers_df}
