"""Maintenance plan optimizer page: CP-SAT schedule, cost and due-date risk."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from flightops.app.common import assumption_box, get_config, inject_css, money, page_header, show_missing_artifact, so_what
from flightops.app.data_access import instance_aircraft, maintenance_plan, scenario_results


def _timeline(plan: pd.DataFrame, aircraft: pd.DataFrame, start_date: str) -> pd.DataFrame:
    frame = plan.merge(aircraft, on=["tail", "fleet", "base", "check_type", "due_day", "due_date", "binding_driver"], how="left", suffixes=("", "_ac"))
    frame["start"] = pd.to_datetime(start_date) + pd.to_timedelta(frame["slot_start_day"], unit="D")
    duration = frame["check_type"].map({"A": 1, "C": 2}).fillna(1).astype(int)
    frame["end"] = frame["start"] + pd.to_timedelta(duration, unit="D")
    frame["label"] = frame.apply(
        lambda row: f"{row['tail']} | {row['check_type']}-check | due day {int(row['due_day'])} | late {int(row['late_days'])}d",
        axis=1,
    )
    frame["status"] = frame["late_days"].map(lambda value: "Late vs due date" if value > 0 else "On or before due date")
    return frame


def render() -> None:
    inject_css()
    cfg = get_config()
    results = scenario_results(cfg)
    aircraft = instance_aircraft(cfg)
    plan = maintenance_plan(cfg, "optimized")
    if not results or aircraft.empty or plan.empty:
        page_header("Maintenance plan optimizer", "CP-SAT aircraft-to-hangar assignment")
        show_missing_artifact("Optimization results", "python scripts/run_optimization.py")
        return

    base = results["baseline_vs_optimized"]
    assumptions = results["assumptions"]
    page_header(
        "Maintenance plan optimizer",
        "CP-SAT assigns aircraft to hangar slots while respecting fleet eligibility, check type, ground time and RUL due dates.",
    )

    cols = st.columns(5)
    cols[0].metric("Aircraft to schedule", assumptions["aircraft_needing_maintenance"])
    cols[1].metric("Hangar slots in horizon", assumptions["hangar_slots"])
    cols[2].metric("Optimized cost", money(base["optimized_cost_usd"]))
    cols[3].metric("Savings vs current practice", money(base["savings_vs_greedy_usd"]), delta=f"{base['savings_vs_greedy_pct']:.1f}%")
    cols[4].metric("Solver time", f"{base['solve_seconds']:.2f}s", help=base["solver_status"])

    st.markdown("### Optimized hangar schedule")
    timeline = _timeline(plan, aircraft, assumptions["start_date"])
    fig = px.timeline(
        timeline,
        x_start="start",
        x_end="end",
        y="hangar",
        color="status",
        hover_name="label",
        color_discrete_map={"On or before due date": "#0f766e", "Late vs due date": "#b91c1c"},
        title="Aircraft assigned to hangar positions (hover for tail, check type and due date)",
    )
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_xaxes(title="Planning date")
    fig.update_layout(height=340, margin=dict(t=45, b=10), legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, width="stretch")

    # One row per aircraft so the schedule can be audited outside the chart.
    table = timeline.sort_values(["slot_start_day", "tail"])[
        [
            "tail",
            "fleet",
            "base",
            "check_type",
            "binding_driver",
            "due_day",
            "due_date",
            "hangar",
            "assigned_slot",
            "slot_start_day",
            "late_days",
            "early_days",
            "total_cost_usd",
            "status",
        ]
    ].rename(
        columns={
            "tail": "Tail",
            "fleet": "Fleet",
            "base": "Base",
            "check_type": "Check",
            "binding_driver": "Binding driver",
            "due_day": "Due day",
            "due_date": "Due date",
            "hangar": "Hangar",
            "assigned_slot": "Slot",
            "slot_start_day": "Start day",
            "late_days": "Late (d)",
            "early_days": "Early (d)",
            "total_cost_usd": "Cost (USD)",
        }
    )
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        height=330,
        column_config={
            "Cost (USD)": st.column_config.NumberColumn(format="$%d"),
            "Due date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        },
    )

    left, right = st.columns([1, 1])
    with left:
        breakdown = pd.DataFrame(
            [
                {"component": "Flying disruption", "usd": results["breakdown_optimized"]["disruption_usd"]},
                {"component": "Early maintenance waste", "usd": results["breakdown_optimized"]["early_maintenance_usd"]},
                {"component": "AOG-risk penalty", "usd": results["breakdown_optimized"]["aog_risk_usd"]},
                {"component": "Unassigned penalty", "usd": results["breakdown_optimized"]["unassigned_penalty_usd"]},
            ]
        )
        fig = px.bar(
            breakdown,
            x="usd",
            y="component",
            orientation="h",
            title="Optimized plan cost breakdown",
            color="usd",
            color_continuous_scale="Teal",
        )
        fig.update_layout(height=300, coloraxis_showscale=False, yaxis_title="", xaxis_tickprefix="$", xaxis_tickformat="~s", margin=dict(t=45, b=10))
        st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown("#### Why the optimizer wins")
        st.markdown(
            f"""
            - Current practice leaves **{base['greedy_unassigned']}** aircraft unassigned; the optimized plan leaves **{base['optimized_unassigned']}**.
            - It cuts early-maintenance waste from **{money(results['breakdown_greedy']['early_maintenance_usd'])}** to **{money(results['breakdown_optimized']['early_maintenance_usd'])}**.
            - It also lowers AOG-risk exposure from **{money(results['breakdown_greedy']['aog_risk_usd'])}** to **{money(results['breakdown_optimized']['aog_risk_usd'])}**.
            - The model is solved to **{base['solver_status'].lower()}** proof within the configured time limit.
            """
        )
        st.caption(
            "The instance is synthetic (15 aircraft, 52 slots, 3 hangars) but follows a CAMO export shape. "
            "Cost figures are planning assumptions, not audited airline actuals."
        )

    assumption_box(
        "Hard constraints: one aircraft per hangar-day, fleet eligibility, A-check vs C-check type, minimum ground time, "
        "and horizon. RUL and check intervals only set the due date; the solver may schedule early or late and pays the "
        "corresponding early-maintenance or escalating AOG-risk penalty."
    )
    so_what(
        f"Approve the optimized plan: it schedules all {len(plan)} aircraft, "
        f"removes the {base['greedy_unassigned']} unassigned aircraft left by earliest-slot practice, and saves "
        f"{money(base['savings_vs_greedy_usd'])} ({base['savings_vs_greedy_pct']:.1f}%) against current practice. "
        "Release slots to the two highest-risk RUL aircraft first, then use the scenarios page to stress-test hangar "
        "loss and demand growth before freezing the plan."
    )
