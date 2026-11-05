"""OTP descriptive analytics, EDA figures and plain-language root-cause summary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from flightops.config import Config
from flightops.data.database import query_df, query_named
from flightops.io import read_json, write_json
from flightops.logging_utils import get_logger

logger = get_logger("analysis.otp")


def _save_bar(series: pd.Series, title: str, ylabel: str, path: Path, color: str = "tab:blue") -> None:
    fig, axis = plt.subplots(figsize=(8.4, 4.0))
    axis.bar(series.index.astype(str), series.to_numpy(), color=color, alpha=0.85)
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.tick_params(axis="x", rotation=35, labelsize=8)
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _segment_query(cfg: Config, dimension: str, min_flights: int = 500) -> pd.DataFrame:
    return query_df(
        cfg,
        f"""
        SELECT
            {dimension} AS segment,
            COUNT(*) AS flights,
            ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
            ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min
        FROM flights
        WHERE {dimension} IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= {min_flights}
        ORDER BY delay_rate DESC
        """,
    )


def _inbound_bucket_query(cfg: Config) -> pd.DataFrame:
    return query_df(
        cfg,
        """
        SELECT
            CASE
                WHEN inbound_known = 0 THEN 'Inbound not yet known'
                WHEN prev_arr_delay_obs < 0 THEN 'Inbound early'
                WHEN prev_arr_delay_obs < 15 THEN 'Inbound 0-14 min late'
                WHEN prev_arr_delay_obs < 30 THEN 'Inbound 15-29 min late'
                WHEN prev_arr_delay_obs < 60 THEN 'Inbound 30-59 min late'
                ELSE 'Inbound 60+ min late'
            END AS segment,
            COUNT(*) AS flights,
            ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
            ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min
        FROM flights
        GROUP BY 1
        ORDER BY delay_rate
        """,
    )


def _describe_segment(frame: pd.DataFrame, base_rate: float, label: str) -> list[str]:
    lines = []
    for _, row in frame.head(3).iterrows():
        lift = row["delay_rate"] / max(base_rate, 1e-9)
        lines.append(
            f"{label} {row['segment']}: {row['delay_rate']:.1%} delay rate vs {base_rate:.1%} overall "
            f"({lift:.2f}x, n={int(row['flights']):,})."
        )
    return lines


def build_otp_insights(cfg: Config) -> dict[str, Any]:
    """Create EDA figures, segment tables and an executive root-cause narrative."""
    figures = cfg.path("paths.figures")
    metrics_dir = cfg.path("paths.metrics")
    overview = query_named(cfg, "otp_overview").iloc[0].to_dict()
    monthly = query_named(cfg, "monthly_otp")
    hourly = query_named(cfg, "hourly_delay")
    carriers = query_named(cfg, "carrier_performance")
    aircraft = query_named(cfg, "aircraft_performance")
    turnaround = query_named(cfg, "turnaround_effect")
    causes = query_named(cfg, "delay_causes")
    routes = query_named(cfg, "top_delay_routes")
    daily = query_named(cfg, "daily_otp")

    _save_bar(monthly.set_index("month")["delay_rate"], "Delay rate by month (seasonality)", "Share of departures >15 min late", figures / "otp_monthly.png", "tab:purple")

    fig, axis1 = plt.subplots(figsize=(8.6, 4.2))
    axis1.plot(hourly["dep_hour"], hourly["delay_rate"], marker="o", color="tab:red")
    axis1.set_xlabel("Scheduled departure hour")
    axis1.set_ylabel("Delay rate", color="tab:red")
    axis1.tick_params(axis="y", labelcolor="tab:red")
    axis1.grid(alpha=0.25)
    axis2 = axis1.twinx()
    axis2.plot(hourly["dep_hour"], hourly["avg_inbound_delay_min"], marker="s", color="tab:blue", alpha=0.7)
    axis2.set_ylabel("Avg inbound delay (min)", color="tab:blue")
    axis2.tick_params(axis="y", labelcolor="tab:blue")
    axis1.set_title("Delay risk compounds through the operating day")
    fig.tight_layout()
    fig.savefig(figures / "otp_hourly.png", dpi=160)
    plt.close(fig)

    _save_bar(carriers.set_index("carrier")["delay_rate"], "Delay rate by carrier", "Delay rate", figures / "otp_carrier.png", "tab:green")
    _save_bar(aircraft.set_index("aircraft_type")["delay_rate"], "Delay rate by aircraft type", "Delay rate", figures / "otp_aircraft.png", "tab:orange")
    _save_bar(turnaround.set_index("scheduled_turn_bucket")["delay_rate"], "Delay rate by scheduled turnaround", "Delay rate", figures / "otp_turnaround.png", "tab:brown")

    fig, axis = plt.subplots(figsize=(7.4, 4.0))
    axis.barh(causes["cause"], causes["delay_minutes"] / 1e6, color="tab:red", alpha=0.8)
    axis.set_xlabel("Attributed delay minutes (millions)")
    axis.set_title("Delay-cause mix: late aircraft and carrier dominate")
    axis.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "otp_delay_causes.png", dpi=160)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(8.6, 4.2))
    top_routes = routes.sort_values("delay_rate", ascending=False).head(15)
    axis.barh((top_routes["origin"] + "-" + top_routes["dest"]), top_routes["delay_rate"], color="tab:cyan")
    axis.set_xlabel("Delay rate")
    axis.set_title("Highest-delay routes (>=500 flights)")
    axis.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "otp_top_routes.png", dpi=160)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(10.0, 3.8))
    axis.plot(daily["flight_date"], daily["otp_rate"], linewidth=0.8, color="tab:blue")
    axis.set_ylabel("Daily OTP rate")
    axis.set_title("Daily on-time performance across 2024")
    axis.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "otp_daily.png", dpi=160)
    plt.close(fig)

    base_rate = float(overview["delay_rate"])
    inbound = _inbound_bucket_query(cfg)
    hourly_seg = _segment_query(cfg, "dep_hour")
    month_seg = _segment_query(cfg, "month")
    carrier_seg = _segment_query(cfg, "carrier")
    aircraft_seg = _segment_query(cfg, "aircraft_type")
    route_seg = _segment_query(cfg, "route", min_flights=500)

    # Segment lift table for the dashboard.
    segment_frames = []
    for name, frame in (
        ("hour", hourly_seg),
        ("month", month_seg),
        ("carrier", carrier_seg),
        ("aircraft_type", aircraft_seg),
        ("route", route_seg),
        ("inbound_delay", inbound),
    ):
        temp = frame.copy()
        temp["dimension"] = name
        temp["lift_vs_base"] = (temp["delay_rate"] / max(base_rate, 1e-9)).round(2)
        segment_frames.append(temp)
    segments = pd.concat(segment_frames, ignore_index=True)
    segments.to_csv(metrics_dir / "otp_segment_lift.csv", index=False)

    shap_path = metrics_dir / "otp_shap_importance.csv"
    shap = pd.read_csv(shap_path) if shap_path.exists() else pd.DataFrame()
    group_importance = {}
    if not shap.empty:
        group_importance = (
            shap.groupby("group")["mean_abs_shap"].sum().sort_values(ascending=False).to_dict()
        )

    # Plain-language RCA. Wording is deliberately associational, not causal.
    peak = hourly_seg[hourly_seg["segment"].between(15, 20)]
    off_peak = hourly_seg[hourly_seg["segment"].between(6, 9)]
    peak_rate = (peak["flights"] * peak["delay_rate"]).sum() / peak["flights"].sum()
    off_peak_rate = (off_peak["flights"] * off_peak["delay_rate"]).sum() / off_peak["flights"].sum()
    worst_month = month_seg.iloc[0]
    best_month = month_seg.iloc[-1]
    worst_carrier = carrier_seg.iloc[0]
    best_carrier = carrier_seg.iloc[-1]
    worst_aircraft = aircraft_seg.iloc[0]
    worst_route = route_seg.iloc[0]
    late_inbound = inbound[inbound["segment"] == "Inbound 60+ min late"]
    early_inbound = inbound[inbound["segment"] == "Inbound early"]
    late_turn = turnaround[turnaround["scheduled_turn_bucket"] == "< 40 min"]
    normal_turn = turnaround[turnaround["scheduled_turn_bucket"] == "90-149 min"]

    narrative = [
        f"Across {int(overview['flights']):,} scheduled flights in 2024, {base_rate:.1%} departed more than 15 minutes late; the cancellation rate was {overview['cancellation_rate']:.1%}.",
        (
            f"Delay risk climbs through the day: the 15:00-20:00 block averages "
            f"{peak_rate:.1%} versus {off_peak_rate:.1%} in the 06:00-09:00 window "
            f"({peak_rate / max(off_peak_rate, 1e-9):.1f}x)."
        ),
        (
            f"The late-aircraft association is visible in the turnaround data: flights with an inbound aircraft already "
            f"60+ minutes late depart late {late_inbound['delay_rate'].iloc[0]:.1%} of the time, versus "
            f"{early_inbound['delay_rate'].iloc[0]:.1%} when the inbound is early. This is associational and is partly "
            f"confounded by scheduled turnaround buffers."
        ),
        (
            f"Scheduled turnarounds below 40 minutes show a {late_turn['delay_rate'].iloc[0]:.1%} delay rate versus "
            f"{normal_turn['delay_rate'].iloc[0]:.1%} for 90-149 minute turnarounds, consistent with little recovery buffer."
        ),
        (
            f"Seasonality matters: month {int(worst_month['segment'])} is the worst month at {worst_month['delay_rate']:.1%} "
            f"versus month {int(best_month['segment'])} at {best_month['delay_rate']:.1%}."
        ),
        (
            f"Carrier spread is material: {worst_carrier['segment']} delays {worst_carrier['delay_rate']:.1%} of departures "
            f"versus {best_carrier['segment']} at {best_carrier['delay_rate']:.1%}."
        ),
        (
            f"The highest-delay high-volume route is {worst_route['segment']} at {worst_route['delay_rate']:.1%} "
            f"across {int(worst_route['flights']):,} flights."
        ),
        (
            f"{worst_aircraft['segment']} aircraft show the highest delay rate among types with >=500 flights "
            f"({worst_aircraft['delay_rate']:.1%}). Aircraft type is an enrichment proxy; it should be validated against the operator's fleet registry."
        ),
    ]
    if group_importance:
        top_group, top_value = next(iter(group_importance.items()))
        total = sum(group_importance.values())
        narrative.insert(
            1,
            f"The gradient-boosted model's SHAP attribution ranks {top_group.lower()} as the largest driver group "
            f"({top_value / total:.0%} of total absolute SHAP), followed by "
            f"{list(group_importance.keys())[1].lower() if len(group_importance) > 1 else 'network effects'}.",
        )

    payload = {
        "overview": overview,
        "monthly": monthly.to_dict(orient="records"),
        "hourly": hourly.to_dict(orient="records"),
        "carriers": carriers.to_dict(orient="records"),
        "aircraft": aircraft.to_dict(orient="records"),
        "turnaround": turnaround.to_dict(orient="records"),
        "delay_causes": causes.to_dict(orient="records"),
        "top_routes": routes.to_dict(orient="records"),
        "inbound_delay": inbound.to_dict(orient="records"),
        "shap_group_importance": group_importance,
        "narrative": narrative,
    }
    write_json(payload, metrics_dir / "otp_insights.json")

    lines = ["# OTP root-cause summary", "", "## What the data says", ""]
    lines += [f"- {item}" for item in narrative]
    lines += ["", "## Model drivers (SHAP)", ""]
    if not shap.empty:
        lines += [
            f"- {row.feature}: mean |SHAP| {row.mean_abs_shap:.4f} ({row.group})"
            for row in shap.head(12).itertuples()
        ]
    lines += [
        "",
        "## Caveats",
        "",
        "- These are associations in observational data, not causal estimates.",
        "- Aircraft type is a deterministic synthetic enrichment because the BTS extract has no equipment field.",
        "- Weather is represented only through season/calendar proxies; no station-level weather feed is joined.",
        "- Delay-cause fields (carrier/weather/NAS/late aircraft) are post-flight attributions and are excluded from model features.",
    ]
    (cfg.path("paths.docs") / "root_cause_summary.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info("OTP insights and root-cause summary written")
    return payload
