"""Fleet health and RUL watchlist page."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from flightops.app.common import assumption_box, get_config, inject_css, page_header, pct, show_missing_artifact, so_what
from flightops.app.data_access import alert_costs, rul_metrics, watchlist
from flightops.data.rul import load_rul


@st.cache_data(ttl=600)
def _cmapss(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)


def render() -> None:
    inject_css()
    cfg = get_config()
    metrics = rul_metrics(cfg)
    watch = watchlist(cfg)
    if not metrics or watch.empty:
        page_header("Fleet health & RUL watchlist", "Engine remaining-useful-life and removal risk")
        show_missing_artifact("RUL model", "python scripts/run_all.py --only-rul")
        return

    page_header(
        "Fleet health & RUL watchlist",
        "NASA C-MAPSS FD001 (simulated turbofan degradation). Predicted RUL is a decision aid, not a release-to-service authority.",
    )
    model = metrics["models"]["lstm"]
    alert = metrics["alerts"]["selected"]
    counts = metrics.get("watchlist_counts", {})
    cols = st.columns(5)
    cols[0].metric("Engines scored", len(watch))
    cols[1].metric("Critical (<=10 cycles)", int(counts.get("Critical - remove now", 0)))
    cols[2].metric("High (<=20 cycles)", int(counts.get("High - schedule shop visit", 0)))
    cols[3].metric("LSTM RMSE", f"{model['rmse']:.1f} cycles")
    cols[4].metric("Asymmetric score", f"{model['nasa_score_mean']:.2f}")

    left, right = st.columns([1.05, 1])
    with left:
        fig = px.histogram(
            watch,
            x="predicted_rul_cycles",
            color="risk_tier",
            nbins=25,
            title="Predicted RUL distribution and risk tiers",
            labels={"predicted_rul_cycles": "Predicted RUL (cycles)", "risk_tier": "Risk tier"},
            color_discrete_map={
                "Critical - remove now": "#b91c1c",
                "High - schedule shop visit": "#ea580c",
                "Watch - plan next visit": "#ca8a04",
                "Serviceable": "#16a34a",
            },
        )
        fig.update_layout(height=360, margin=dict(t=45, b=10), legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, width="stretch")
    with right:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=watch["true_rul_cycles"], y=watch["predicted_rul_cycles"], mode="markers", marker=dict(size=7, color="#0f172a", opacity=0.7), name="Engines"))
        limit = float(max(watch["true_rul_cycles"].max(), watch["predicted_rul_cycles"].max())) * 1.05
        fig.add_trace(go.Scatter(x=[0, limit], y=[0, limit], mode="lines", line=dict(color="red", dash="dash"), name="Perfect"))
        fig.update_layout(title="Predicted vs true RUL (test engines)", xaxis_title="True RUL (cycles)", yaxis_title="Predicted RUL (cycles)", height=360, margin=dict(t=45, b=10))
        st.plotly_chart(fig, width="stretch")

    st.markdown("### Alert economics")
    cost = alert_costs(cfg)
    left, right = st.columns([1.2, 1])
    with left:
        if not cost.empty:
            fig = px.line(cost, x="threshold_cycles", y="total_cost_usd", title="RUL alert threshold: early removal vs unscheduled removal cost")
            fig.add_vline(x=metrics["alerts"]["selected_threshold_cycles"], line_dash="dash", line_color="red", annotation_text="Selected")
            fig.add_vline(x=metrics["alerts"]["cost_optimal_threshold_cycles"], line_dash="dot", line_color="green", annotation_text="Cost-optimal")
            fig.update_layout(height=340, yaxis_tickprefix="$", yaxis_tickformat="~s", margin=dict(t=45, b=10))
            st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown(
            f"""
            - Selected threshold: **{metrics['alerts']['selected_threshold_cycles']} cycles**
            - Precision **{pct(alert['precision'])}**, recall **{pct(alert['recall'])}**
            - Expected cost at selected threshold: **${alert['total_cost_usd']/1e6:.2f}M**
            - Cost-optimal threshold: **{metrics['alerts']['cost_optimal_threshold_cycles']} cycles**
            - Savings vs no alerts: **${metrics['alerts']['savings_vs_no_alert_usd']/1e6:.2f}M**
            - 90% conformal interval: **+/-{metrics['conformal']['q_cycles']:.1f} cycles**, coverage **{pct(metrics['conformal']['test_coverage'])}**
            """
        )
        st.caption("Costs: $250k unscheduled removal vs $75k planned early removal. Illustrative, configurable planning assumptions.")

    st.markdown("### Watchlist")
    tier_filter = st.multiselect("Filter risk tier", sorted(watch["risk_tier"].unique()), default=sorted(watch["risk_tier"].unique()))
    shown = watch[watch["risk_tier"].isin(tier_filter)].sort_values("predicted_rul_cycles")
    st.dataframe(
        shown[
            [
                "unit",
                "last_cycle",
                "predicted_rul_cycles",
                "prediction_low",
                "prediction_high",
                "true_rul_cycles",
                "abs_error_cycles",
                "risk_tier",
                "recommended_action",
            ]
        ].rename(
            columns={
                "unit": "Engine unit",
                "last_cycle": "Last cycle",
                "predicted_rul_cycles": "Pred RUL",
                "prediction_low": "P10 lower",
                "prediction_high": "P90 upper",
                "true_rul_cycles": "True RUL (eval only)",
                "abs_error_cycles": "Abs error",
                "risk_tier": "Risk tier",
                "recommended_action": "Recommended action",
            }
        ),
        width="stretch",
        hide_index=True,
        height=320,
    )

    st.markdown("### Engine drill-down")
    unit = st.selectbox("Select an engine from the test cohort", sorted(watch["unit"].astype(int).tolist()))
    selected = watch[watch["unit"] == unit].iloc[0]
    trajectory = _cmapss(str(cfg.path("data.rul.parquet")))
    unit_frame = trajectory[(trajectory["split"] == "test") & (trajectory["unit"] == unit)]
    sensor = st.selectbox("Sensor", ["sensor_11", "sensor_4", "sensor_15", "sensor_21"], index=0)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=unit_frame["cycle"], y=unit_frame[sensor], mode="lines", name=sensor, line=dict(color="#0f172a")))
    fig.add_vline(x=int(selected["last_cycle"]), line_dash="dash", line_color="red", annotation_text="Scored cycle")
    fig.update_layout(title=f"Engine {unit}: {sensor} degradation trajectory", xaxis_title="Cycle", yaxis_title="Sensor value", height=340, margin=dict(t=45, b=10))
    st.plotly_chart(fig, width="stretch")

    cols = st.columns(4)
    cols[0].metric("Predicted RUL", f"{selected['predicted_rul_cycles']:.1f} cycles")
    cols[1].metric("90% interval", f"{selected['prediction_low']:.1f} - {selected['prediction_high']:.1f}")
    cols[2].metric("Risk tier", selected["risk_tier"])
    cols[3].metric("True RUL (eval only)", f"{selected['true_rul_cycles']:.1f}")
    st.info(selected["recommended_action"])
    assumption_box(
        "C-MAPSS is a simulated turbofan degradation dataset, not a fleet feed. The model is trained on run-to-failure "
        "trajectories and evaluated on the official truncated test engines. In service, RUL must be combined with "
        "borescope, oil analysis, and OEM limits, and signed off by a licensed engineer."
    )
    so_what(
        f"{int(counts.get('Critical - remove now', 0))} engines are inside the 10-cycle critical zone and "
        f"{int(counts.get('High - schedule shop visit', 0))} more are inside 20 cycles. Reserve shop capacity for the "
        "critical group now, use the 20-cycle group to pull forward planned visits, and re-score weekly. The alert "
        "threshold is a CAMO policy choice: the selected 20-cycle rule produces the same cost as the 19-cycle optimum "
        "and is simpler to communicate to crews."
    )
