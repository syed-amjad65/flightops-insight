"""Flight-level delay-risk predictor with local SHAP explanation."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from flightops.app.common import assumption_box, get_config, inject_css, page_header, pct, show_missing_artifact, so_what
from flightops.app.data_access import otp_insights
from flightops.data.aircraft import HUBS, TYPE_ATTRIBUTES, is_holiday
from flightops.models.otp_predict import load_predictor, risk_tier


@st.cache_resource
def _predictor():
    return load_predictor(get_config())


def _friendly(feature: str) -> str:
    mapping = {
        "prev_dep_delay_min": "Inbound departure delay",
        "prev_arr_delay_obs": "Inbound arrival delay",
        "sched_turn_min": "Scheduled turnaround",
        "turnaround_buffer_min": "Turnaround buffer above minimum",
        "dep_hour": "Departure hour",
        "hour_sin": "Time of day",
        "hour_cos": "Time of day",
        "carrier": "Carrier",
        "origin": "Origin airport",
        "dest": "Destination airport",
        "route": "Route",
        "origin_hour_departures": "Origin departures in the hour",
        "origin_daily_departures": "Origin daily departures",
        "route_daily_frequency": "Route frequency",
        "doy_cos": "Season",
        "doy_sin": "Season",
        "distance_mi": "Stage length",
        "crs_elapsed_min": "Scheduled block time",
        "aircraft_type": "Aircraft type",
        "aircraft_family": "Aircraft family",
    }
    return mapping.get(feature, feature.replace("_", " ").title())


def _row_from_inputs(inputs: dict) -> pd.DataFrame:
    flight_date: date = inputs["flight_date"]
    aircraft_type = inputs["aircraft_type"]
    family, seats, min_turn = TYPE_ATTRIBUTES.get(aircraft_type, ("narrowbody", 160, 45))
    carrier = inputs["carrier"]
    origin = inputs["origin"]
    dest = inputs["dest"]
    inbound_known = int(inputs["inbound_known"])
    inbound_arr = float(inputs["inbound_arr_delay"]) if inbound_known else np.nan
    inbound_dep = float(inputs["inbound_dep_delay"]) if inbound_known else np.nan
    row = {
        "month": flight_date.month,
        "day_of_week": flight_date.isoweekday(),
        "day_of_year": flight_date.timetuple().tm_yday,
        "dep_hour": int(inputs["dep_hour"]),
        "dep_minute": int(inputs["dep_minute"]),
        "is_weekend": int(flight_date.isoweekday() >= 6),
        "is_holiday": int(is_holiday(pd.Series([pd.Timestamp(flight_date)])).iloc[0]),
        "distance_mi": float(inputs["distance_mi"]),
        "crs_elapsed_min": float(inputs["crs_elapsed_min"]),
        "seats": seats,
        "min_turn_min": min_turn,
        "sched_turn_min": float(inputs["sched_turn_min"]),
        "prev_dep_delay_min": inbound_dep,
        "prev_arr_delay_obs": inbound_arr,
        "inbound_known": inbound_known,
        "prev_cancelled": 0,
        "origin_hour_departures": int(inputs["origin_hour_departures"]),
        "origin_daily_departures": int(inputs["origin_daily_departures"]),
        "route_daily_frequency": int(inputs["route_daily_frequency"]),
        "is_hub_origin": int((carrier, origin) in {(c, a) for c, airports in HUBS.items() for a in airports}),
        "is_hub_dest": int((carrier, dest) in {(c, a) for c, airports in HUBS.items() for a in airports}),
        "carrier": carrier,
        "origin": origin,
        "dest": dest,
        "aircraft_type": aircraft_type,
        "aircraft_family": family,
        "origin_state": "",
        "dest_state": "",
        "flight_date": pd.Timestamp(flight_date),
    }
    return pd.DataFrame([row])


def render() -> None:
    inject_css()
    cfg = get_config()
    insights = otp_insights(cfg)
    if not insights:
        page_header("Delay-risk predictor", "Score a single flight before departure")
        show_missing_artifact("OTP model", "python scripts/run_all.py --skip-optimize")
        return
    try:
        predictor = _predictor()
    except Exception as exc:  # pragma: no cover - UI guard
        page_header("Delay-risk predictor", "Score a single flight before departure")
        st.error(f"Could not load the OTP model: {exc}")
        return

    page_header(
        "Delay-risk predictor",
        "Day-of decision support. Scores at a documented T-90 minute cutoff; inbound actuals are used only if observable by then.",
    )
    page = insights["carriers"]
    carriers = [row["carrier"] for row in page]
    airports = sorted({row["origin"] for row in insights["top_routes"]} | {row["dest"] for row in insights["top_routes"]})

    with st.form("predict_form"):
        cols = st.columns(4)
        carrier = cols[0].selectbox("Carrier", carriers, index=0)
        origin = cols[1].selectbox("Origin", airports, index=0)
        dest = cols[2].selectbox("Destination", airports, index=min(1, len(airports) - 1))
        aircraft_type = cols[3].selectbox("Aircraft type", sorted(TYPE_ATTRIBUTES), index=1)
        cols = st.columns(4)
        flight_date = cols[0].date_input("Flight date", value=date(2024, 11, 15))
        dep_hour = cols[1].slider("Scheduled departure hour", 0, 23, 17)
        dep_minute = cols[2].selectbox("Minute", [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55], index=0)
        sched_turn = cols[3].number_input("Scheduled turnaround (min)", min_value=20, max_value=600, value=45, step=5)
        cols = st.columns(4)
        distance = cols[0].number_input("Distance (mi)", min_value=50, max_value=6000, value=900, step=50)
        block = cols[1].number_input("Scheduled block time (min)", min_value=30, max_value=720, value=150, step=5)
        inbound_known = cols[2].checkbox("Inbound aircraft already arrived (actual known)", value=True)
        inbound_arr = cols[3].number_input("Inbound arrival delay (min)", min_value=-60, max_value=360, value=5, step=5)
        cols = st.columns(4)
        inbound_dep = cols[0].number_input("Inbound departure delay (min)", min_value=-60, max_value=360, value=5, step=5)
        hour_deps = cols[1].number_input("Departures at origin this hour", min_value=1, max_value=80, value=20)
        daily_deps = cols[2].number_input("Departures at origin today", min_value=10, max_value=1200, value=250)
        route_freq = cols[3].number_input("Flights on route today", min_value=1, max_value=30, value=4)
        submitted = st.form_submit_button("Score flight", width="stretch")

    inputs = {
        "carrier": carrier,
        "origin": origin,
        "dest": dest,
        "aircraft_type": aircraft_type,
        "flight_date": flight_date,
        "dep_hour": dep_hour,
        "dep_minute": dep_minute,
        "sched_turn_min": sched_turn,
        "distance_mi": distance,
        "crs_elapsed_min": block,
        "inbound_known": inbound_known,
        "inbound_arr_delay": inbound_arr,
        "inbound_dep_delay": inbound_dep,
        "origin_hour_departures": hour_deps,
        "origin_daily_departures": daily_deps,
        "route_daily_frequency": route_freq,
    }
    row = _row_from_inputs(inputs)
    probability = float(predictor.predict(row)[0])
    tier = risk_tier(probability)
    threshold = predictor.artifacts.threshold

    left, right = st.columns([0.9, 1.1])
    with left:
        gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=probability * 100,
                number={"suffix": "%", "font": {"size": 42}},
                title={"text": f"Delay risk<br><span style='font-size:0.8em'>Risk tier: {tier}</span>"},
                gauge={
                    "axis": {"range": [0, 100], "ticksuffix": "%"},
                    "bar": {"color": "#0f172a"},
                    "steps": [
                        {"range": [0, 25], "color": "#dcfce7"},
                        {"range": [25, 50], "color": "#fef9c3"},
                        {"range": [50, 75], "color": "#fed7aa"},
                        {"range": [75, 100], "color": "#fecaca"},
                    ],
                    "threshold": {"line": {"color": "red", "width": 3}, "thickness": 0.8, "value": threshold * 100},
                },
            )
        )
        gauge.update_layout(height=310, margin=dict(t=50, b=10, l=20, r=20))
        st.plotly_chart(gauge, width="stretch")
        st.caption(f"Recommended targeting threshold: {threshold:.0%}. Red line = operating threshold.")

    with right:
        st.markdown(f"### {tier} risk")
        st.markdown(
            f"Estimated probability of departing >15 minutes late: **{pct(probability)}** "
            f"(model PR-AUC {predictor.artifacts.metrics['models']['lightgbm']['pr_auc']:.3f}, "
            f"ROC-AUC {predictor.artifacts.metrics['models']['lightgbm']['roc_auc']:.3f})."
        )
        explanation = predictor.explain(row, top_n=8)
        explanation["driver"] = explanation["feature"].map(_friendly)
        explanation["value"] = explanation["value"].astype(str)
        explanation["impact"] = explanation["shap_value"].map(lambda v: f"{v:+.3f}")
        st.markdown("#### Why this flight?")
        st.dataframe(
            explanation[["driver", "value", "impact"]].rename(columns={"value": "feature value", "impact": "SHAP impact"}),
            width="stretch",
            hide_index=True,
        )
        top = explanation.iloc[0]["feature"] if not explanation.empty else ""
        action = (
            "Open a recovery case now: protect the turn, confirm crew and gate, and identify a swap candidate."
            if probability >= threshold
            else "Keep on watch: re-score when the inbound aircraft departs or arrives; no intervention yet."
        )
        st.markdown(f"**Primary driver:** {_friendly(top)}. {action}")

    # Sensitivity to the two levers an OCC can actually move.
    scenarios = []
    for inbound in [0, 15, 30, 60, 90]:
        for turn in [30, 45, 60, 90]:
            variant = dict(inputs)
            variant["inbound_arr_delay"] = inbound
            variant["inbound_dep_delay"] = inbound
            variant["sched_turn_min"] = turn
            p = float(predictor.predict(_row_from_inputs(variant))[0])
            scenarios.append({"inbound_delay": inbound, "scheduled_turn": turn, "risk": p})
    sensitivity = pd.DataFrame(scenarios)
    fig = go.Figure()
    for turn, group in sensitivity.groupby("scheduled_turn"):
        fig.add_trace(go.Scatter(x=group["inbound_delay"], y=group["risk"], mode="lines+markers", name=f"{turn} min turn"))
    fig.update_layout(
        title="Risk sensitivity: inbound delay vs scheduled turnaround",
        xaxis_title="Inbound arrival delay (min)",
        yaxis_title="Predicted delay risk",
        yaxis_tickformat=".0%",
        height=360,
        legend_title="Scheduled turn",
    )
    st.plotly_chart(fig, width="stretch")

    assumption_box(
        "Prediction is made 90 minutes before scheduled departure. Inbound actuals are used only when the inbound "
        "aircraft had arrived by that cutoff; otherwise the model falls back to schedule, route, carrier, season and "
        "congestion features. Aircraft type is a documented synthetic enrichment in the BTS dataset."
    )
    so_what(
        f"This flight is **{tier.lower()} risk** at {pct(probability)}. The largest modelled driver is "
        f"**{_friendly(top)}**. If the inbound is running late, adding turnaround buffer or swapping the aircraft "
        "reduces predicted risk more than any schedule-only action. Use the sensitivity chart to choose the cheapest "
        "intervention before committing crew or a spare aircraft."
    )
