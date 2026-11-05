"""OTP overview page: network, time-of-day, seasonality and model performance."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from flightops.app.common import get_config, inject_css, money, page_header, pct, show_missing_artifact, so_what
from flightops.app.data_access import filtered_otp, named_query, otp_insights, otp_metrics


def _filters(insights: dict) -> tuple[list[str], list[int]]:
    carriers = [row["carrier"] for row in insights.get("carriers", [])]
    with st.sidebar:
        st.markdown("### OTP filters")
        selected_carriers = st.multiselect("Carrier", carriers, default=[])
        selected_months = st.multiselect("Month", list(range(1, 13)), default=[])
        st.caption("Filters apply to the overview and network charts.")
    return selected_carriers, selected_months


def render() -> None:
    inject_css()
    cfg = get_config()
    metrics = otp_metrics(cfg)
    insights = otp_insights(cfg)
    if not metrics or not insights:
        page_header("OTP overview", "On-time performance and delay-risk diagnostics")
        show_missing_artifact("OTP analytics", "python scripts/run_all.py --skip-train")
        return

    page_header(
        "OTP overview",
        "7.1M BTS flight legs (2024). Where delay risk concentrates, what drives it, and what the model adds.",
    )
    selected_carriers, selected_months = _filters(insights)
    filtered = filtered_otp(cfg, selected_carriers, selected_months)
    overview = filtered["overview"]

    cols = st.columns(5)
    cols[0].metric("Flights", f"{int(overview['flights']):,}")
    cols[1].metric("OTP rate", pct(1 - overview["delay_rate"]))
    cols[2].metric("Delay rate (>15m)", pct(overview["delay_rate"]))
    cols[3].metric("Avg departure delay", f"{overview['avg_dep_delay_min']:.1f} min")
    cols[4].metric("Cancellation rate", pct(overview["cancellation_rate"]))

    st.markdown("### Where the risk sits")
    tab1, tab2, tab3 = st.tabs(["Network & time", "Turnaround & root cause", "Model performance"])

    with tab1:
        left, right = st.columns([1.15, 1])
        with left:
            monthly = filtered["monthly"]
            fig = px.bar(
                monthly,
                x="month",
                y="delay_rate",
                labels={"month": "Month", "delay_rate": "Delay rate"},
                title="Seasonality: delay rate by month",
                color="delay_rate",
                color_continuous_scale="OrRd",
            )
            fig.update_layout(height=360, coloraxis_showscale=False, margin=dict(t=45, b=10))
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(fig, width="stretch")
        with right:
            hourly = filtered["hourly"]
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            fig.add_trace(
                go.Bar(x=hourly["dep_hour"], y=hourly["flights"], name="Flights", marker_color="#cbd5e1", opacity=0.55),
                secondary_y=True,
            )
            fig.add_trace(
                go.Scatter(x=hourly["dep_hour"], y=hourly["delay_rate"], name="Delay rate", mode="lines+markers", line=dict(color="#dc2626", width=3)),
                secondary_y=False,
            )
            fig.update_layout(title="Time of day: risk compounds in the afternoon", height=360, margin=dict(t=45, b=10), legend=dict(orientation="h"))
            fig.update_yaxes(tickformat=".0%", secondary_y=False, title_text="Delay rate")
            fig.update_yaxes(showgrid=False, secondary_y=True, title_text="Flights")
            st.plotly_chart(fig, width="stretch")

        left, right = st.columns(2)
        with left:
            carriers = filtered["carriers"].sort_values("delay_rate")
            fig = px.bar(carriers, x="delay_rate", y="carrier", orientation="h", title="Carrier spread", color="delay_rate", color_continuous_scale="Blues")
            fig.update_layout(height=360, coloraxis_showscale=False, yaxis_title="", margin=dict(t=45, b=10))
            fig.update_xaxes(tickformat=".0%")
            st.plotly_chart(fig, width="stretch")
        with right:
            aircraft = pd.DataFrame(insights["aircraft"]).sort_values("delay_rate")
            fig = px.bar(aircraft, x="delay_rate", y="aircraft_type", orientation="h", title="Aircraft type (synthetic enrichment)", color="delay_rate", color_continuous_scale="Teal")
            fig.update_layout(height=360, coloraxis_showscale=False, yaxis_title="", margin=dict(t=45, b=10))
            fig.update_xaxes(tickformat=".0%")
            st.plotly_chart(fig, width="stretch")

        routes = pd.DataFrame(insights["top_routes"]).sort_values("delay_rate", ascending=False).head(15)
        routes["route"] = routes["origin"] + "-" + routes["dest"]
        fig = px.bar(routes, x="route", y="delay_rate", title="Highest-delay routes with >=500 flights", color="delay_rate", color_continuous_scale="Reds")
        fig.update_layout(height=350, coloraxis_showscale=False, margin=dict(t=45, b=10))
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(fig, width="stretch")

    with tab2:
        left, right = st.columns(2)
        with left:
            turnaround = pd.DataFrame(insights["turnaround"])
            order = ["< 40 min", "40-59 min", "60-89 min", "90-149 min", "150+ min", "No inbound link"]
            turnaround["bucket"] = pd.Categorical(turnaround["scheduled_turn_bucket"], categories=order, ordered=True)
            turnaround = turnaround.sort_values("bucket")
            fig = px.bar(turnaround, x="bucket", y="delay_rate", title="Short scheduled turnarounds carry more risk", color="delay_rate", color_continuous_scale="Oranges")
            fig.update_layout(height=350, coloraxis_showscale=False, xaxis_title="", margin=dict(t=45, b=10))
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(fig, width="stretch")
        with right:
            inbound = pd.DataFrame(insights["inbound_delay"]).sort_values("delay_rate")
            fig = px.bar(inbound, x="delay_rate", y="segment", orientation="h", title="Inbound aircraft delay is a leading indicator", color="delay_rate", color_continuous_scale="Purples")
            fig.update_layout(height=350, coloraxis_showscale=False, yaxis_title="", margin=dict(t=45, b=10))
            fig.update_xaxes(tickformat=".0%")
            st.plotly_chart(fig, width="stretch")

        causes = pd.DataFrame(insights["delay_causes"])
        fig = px.bar(causes, x="delay_minutes", y="cause", orientation="h", title="Attributed delay minutes, 2024", color="delay_minutes", color_continuous_scale="Reds")
        fig.update_layout(height=320, coloraxis_showscale=False, yaxis_title="", margin=dict(t=45, b=10))
        st.plotly_chart(fig, width="stretch")

        st.markdown("#### Root cause in plain language")
        for line in insights.get("narrative", []):
            st.markdown(f"- {line}")

        shap_path = cfg.path("paths.figures") / "otp_shap_bar.png"
        if shap_path.exists():
            st.image(str(shap_path), caption="Global SHAP importance (held-out Nov/Dec sample)", width="stretch")

    with tab3:
        model = metrics["models"]["lightgbm"]
        baseline = metrics["models"]["route_hour_baseline"]
        logistic = metrics["models"]["logistic_regression"]
        cols = st.columns(5)
        cols[0].metric("ROC-AUC", f"{model['roc_auc']:.3f}", help=f"Baseline {baseline['roc_auc']:.3f}")
        cols[1].metric("PR-AUC", f"{model['pr_auc']:.3f}", help=f"Baseline {baseline['pr_auc']:.3f}")
        cols[2].metric("Precision @0.5", f"{model['precision']:.3f}")
        cols[3].metric("Recall @0.5", f"{model['recall']:.3f}")
        cols[4].metric("Brier / ECE", f"{model['brier']:.3f} / {model['ece']:.3f}")

        comparison = pd.DataFrame(
            {
                "model": ["Route-hour baseline", "Logistic regression", "LightGBM"],
                "roc_auc": [baseline["roc_auc"], logistic["roc_auc"], model["roc_auc"]],
                "pr_auc": [baseline["pr_auc"], logistic["pr_auc"], model["pr_auc"]],
                "brier": [baseline["brier"], logistic["brier"], model["brier"]],
                "top_decile_lift": [baseline["top_decile_lift"], logistic["top_decile_lift"], model["top_decile_lift"]],
            }
        )
        st.dataframe(comparison.style.format({"roc_auc": "{:.3f}", "pr_auc": "{:.3f}", "brier": "{:.3f}", "top_decile_lift": "{:.2f}x"}), width="stretch", hide_index=True)

        left, right = st.columns(2)
        with left:
            p = cfg.path("paths.figures") / "otp_calibration.png"
            if p.exists():
                st.image(str(p), caption="Calibration on the held-out period", width="stretch")
        with right:
            p = cfg.path("paths.figures") / "otp_roc_pr.png"
            if p.exists():
                st.image(str(p), caption="ROC and precision-recall curves", width="stretch")

        st.markdown("#### Financial framing (planning scenario, not a causal claim)")
        business = metrics["business_case_top_decile"]
        base = next((s for s in business["scenarios"] if abs(s["prevention_rate"] - 0.10) < 1e-9), business["scenarios"][0])
        cols = st.columns(4)
        cols[0].metric("Flagged flights (test)", f"{business['flagged']:,}")
        cols[1].metric("Precision", pct(business["precision"]))
        cols[2].metric("Gross delay cost avoided @10% prevention", money(base["gross_delay_cost_avoided_usd"]))
        cols[3].metric("Net benefit after intervention cost", money(base["net_benefit_usd"]))
        st.caption(
            "Assumes $85/min delay cost + $1,200 passenger re-accommodation, $250 intervention cost, "
            "and 10% of flagged delays preventable. These are stated planning assumptions, not measured savings."
        )

    so_what(
        "Target the afternoon bank and short-turn flights first: the 15:00-20:00 block runs at about 2.7x the "
        "06:00-09:00 delay rate, and turnarounds under 40 minutes run at 27.8% versus 15.5% for 90-149 minute turns, "
        "while late inbound aircraft and network congestion amplify the effect. A top-decile risk list captures about "
        "44% of all delays with 73% precision, which is "
        "the right shape for a finite OCC intervention team. Use the predictor page to test a specific flight."
    )
