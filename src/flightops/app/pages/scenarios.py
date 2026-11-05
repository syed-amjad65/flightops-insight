"""Scenario comparison page: disruption stress tests and policy sensitivity."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from flightops.app.common import assumption_box, get_config, inject_css, money, page_header, show_missing_artifact, so_what
from flightops.app.data_access import scenario_results, sensitivity


def render() -> None:
    inject_css()
    cfg = get_config()
    results = scenario_results(cfg)
    sensitivity_df = sensitivity(cfg)
    if not results:
        page_header("Scenario comparison", "Stress-test the maintenance plan")
        show_missing_artifact("Scenario results", "python scripts/run_optimization.py")
        return

    base = results["baseline_vs_optimized"]
    page_header(
        "Scenario comparison",
        "What happens to cost, unassigned aircraft and AOG exposure when hangar capacity, RUL or demand changes?",
    )

    scenarios = pd.DataFrame(results["scenarios"])
    disruptions = scenarios[scenarios["scenario"].str.startswith("Disruption")].copy()
    disruptions["case"] = disruptions["scenario"].str.replace("Disruption: ", "", regex=False).str.replace("_", " ").str.title()
    worst = disruptions.loc[disruptions["total_cost_usd"].idxmax()] if not disruptions.empty else None
    if worst is not None:
        cols = st.columns(4)
        cols[0].metric("Baseline optimized cost", money(base["optimized_cost_usd"]))
        cols[1].metric("Worst disruption cost", money(worst["total_cost_usd"]), delta=f"+{money(worst['total_cost_usd'] - base['optimized_cost_usd'])}")
        cols[2].metric("Worst case", str(worst["case"]))
        cols[3].metric("Aircraft unassigned in worst case", int(worst["unassigned"]))

    st.markdown("### Disruption stress tests")
    if not disruptions.empty:
        left, right = st.columns([1.15, 1])
        with left:
            compare = disruptions.melt(
                id_vars=["case"],
                value_vars=["greedy_cost_usd", "total_cost_usd"],
                var_name="strategy",
                value_name="cost_usd",
            )
            compare["strategy"] = compare["strategy"].map({"greedy_cost_usd": "Current practice", "total_cost_usd": "CP-SAT optimized"})
            fig = px.bar(
                compare,
                x="case",
                y="cost_usd",
                color="strategy",
                barmode="group",
                title="Optimized vs current practice under each disruption",
                color_discrete_map={"Current practice": "#94a3b8", "CP-SAT optimized": "#0f766e"},
            )
            fig.update_layout(height=360, yaxis_tickprefix="$", yaxis_tickformat="~s", xaxis_title="", legend_title="", margin=dict(t=45, b=10))
            st.plotly_chart(fig, width="stretch")
        with right:
            fig = go.Figure(
                go.Scatter(
                    x=disruptions["case"],
                    y=disruptions["total_cost_usd"],
                    mode="markers+lines",
                    marker=dict(size=12, color=disruptions["unassigned"], colorscale="Reds", showscale=True, colorbar=dict(title="Unassigned")),
                    line=dict(color="#334155", width=2),
                    hovertemplate="%{x}<br>Cost $%{y:,.0f}<extra></extra>",
                )
            )
            fig.add_hline(y=base["optimized_cost_usd"], line_dash="dash", line_color="#0f766e", annotation_text="Baseline")
            fig.update_layout(title="Cost and unassigned aircraft by disruption", yaxis_tickprefix="$", yaxis_tickformat="~s", xaxis_title="", height=360, margin=dict(t=45, b=10))
            st.plotly_chart(fig, width="stretch")

    st.markdown("### Scenario table")
    shown = scenarios.copy()
    shown["uplift_vs_baseline_usd"] = shown["total_cost_usd"] - base["optimized_cost_usd"]
    shown = shown[
        [
            "scenario",
            "strategy",
            "assigned",
            "unassigned",
            "total_cost_usd",
            "uplift_vs_baseline_usd",
            "disruption_usd",
            "early_maintenance_usd",
            "aog_risk_usd",
            "unassigned_penalty_usd",
            "solve_seconds",
        ]
    ].rename(
        columns={
            "scenario": "Scenario",
            "strategy": "Strategy",
            "assigned": "Assigned",
            "unassigned": "Unassigned",
            "total_cost_usd": "Total cost",
            "uplift_vs_baseline_usd": "Uplift vs baseline",
            "disruption_usd": "Disruption",
            "early_maintenance_usd": "Early maint.",
            "aog_risk_usd": "AOG risk",
            "unassigned_penalty_usd": "Unassigned penalty",
            "solve_seconds": "Solve (s)",
        }
    )
    st.dataframe(
        shown,
        width="stretch",
        hide_index=True,
        column_config={
            "Total cost": st.column_config.NumberColumn(format="$%d"),
            "Uplift vs baseline": st.column_config.NumberColumn(format="$%d"),
            "Disruption": st.column_config.NumberColumn(format="$%d"),
            "Early maint.": st.column_config.NumberColumn(format="$%d"),
            "AOG risk": st.column_config.NumberColumn(format="$%d"),
            "Unassigned penalty": st.column_config.NumberColumn(format="$%d"),
        },
    )

    st.markdown("### Sensitivity: AOG-risk penalty")
    if not sensitivity_df.empty:
        left, right = st.columns([1.2, 1])
        with left:
            fig = px.line(
                sensitivity_df,
                x="aog_risk_penalty_usd_per_day",
                y="total_cost_usd",
                markers=True,
                title="Plan cost rises with the policy value placed on AOG risk",
            )
            fig.update_layout(height=330, yaxis_tickprefix="$", yaxis_tickformat="~s", xaxis_title="AOG-risk penalty (USD per aircraft-day)", margin=dict(t=45, b=10))
            st.plotly_chart(fig, width="stretch")
        with right:
            st.markdown("#### Reading the curve")
            st.markdown(
                "- At a low AOG penalty the plan tolerates more late days because flying disruption dominates.\n"
                "- As the penalty rises, the optimizer buys earlier slots and accepts more early-maintenance waste.\n"
                "- The selected policy value is a CAMO risk-appetite decision, not a model output. Test it with the safety team before freezing the plan."
            )
            st.dataframe(
                sensitivity_df.rename(
                    columns={
                        "aog_risk_penalty_usd_per_day": "Penalty/day",
                        "total_cost_usd": "Cost",
                        "assigned": "Assigned",
                        "unassigned": "Unassigned",
                        "aog_risk_usd": "AOG risk",
                        "early_maintenance_usd": "Early maint.",
                    }
                ),
                width="stretch",
                hide_index=True,
                column_config={"Penalty/day": st.column_config.NumberColumn(format="$%d"), "Cost": st.column_config.NumberColumn(format="$%d"), "AOG risk": st.column_config.NumberColumn(format="$%d"), "Early maint.": st.column_config.NumberColumn(format="$%d")},
            )

    assumption_box(
        "Scenarios are deterministic perturbations of the same synthetic instance: H3 closure, +25% RUL stress, and +10% "
        "flying demand. They are stress tests, not forecasts. Re-run the solver on real CAMO data before making a "
        "serviceability decision."
    )
    if worst is not None:
        so_what(
            f"The plan is most sensitive to RUL stress: the worst case costs {money(worst['total_cost_usd'])} with "
            f"{int(worst['unassigned'])} aircraft unassigned. Protect one flexible C-check slot and pre-agree an aircraft "
            "swap list before the demand-surge season; a hangar closure alone is absorbable, but a simultaneous RUL shock is not."
        )
