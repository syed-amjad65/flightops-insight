"""Scenario runner for the maintenance optimizer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from flightops.config import Config, ensure_directories
from flightops.io import write_json
from flightops.logging_utils import get_logger, log_section
from flightops.optimization.maintenance_planner import (
    MaintenancePlan,
    defer_all_plan,
    greedy_plan,
    plan_frame,
    solve_maintenance_plan,
)
from flightops.optimization.synthetic_fleet import build_instance, instance_frames

logger = get_logger("optimization.scenarios")


def _plan_record(scenario: str, plan: MaintenancePlan) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "strategy": plan.status,
        "assigned": plan.assigned_count,
        "unassigned": plan.unassigned_count,
        "total_cost_usd": round(plan.total_cost_usd, 0),
        **{key: round(value, 0) for key, value in plan.breakdown.items()},
        "solve_seconds": plan.solve_seconds,
        "objective_value": plan.objective_value,
    }


def _gantt(instance, plan: MaintenancePlan, path: Path) -> None:
    slots = {slot.slot_id: slot for slot in instance.slots}
    fig, axis = plt.subplots(figsize=(10.5, 4.6))
    hangars = sorted({slot.hangar for slot in instance.slots})
    hangar_index = {hangar: index for index, hangar in enumerate(hangars)}
    colors = {"A": "tab:blue", "C": "tab:orange"}
    for tail, slot_id in plan.assignments.items():
        slot = slots[slot_id]
        y = hangar_index[slot.hangar]
        axis.barh(
            y,
            slot.duration_days,
            left=slot.start_day,
            height=0.55,
            color=colors.get(slot.check_type, "tab:gray"),
            alpha=0.85,
            edgecolor="black",
            linewidth=0.4,
        )
        axis.text(
            slot.start_day + slot.duration_days / 2,
            y,
            tail,
            ha="center",
            va="center",
            fontsize=7,
            color="white" if slot.check_type == "A" else "black",
        )
    axis.set_yticks(range(len(hangars)))
    axis.set_yticklabels(hangars)
    axis.set_xlabel("Planning day")
    axis.set_ylabel("Hangar")
    axis.set_title("Optimized maintenance schedule (A=blue, C=orange)")
    axis.set_xlim(0, instance.horizon_days)
    axis.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run_optimization_scenarios(cfg: Config, force: bool = False) -> dict[str, Any]:
    """Run baseline, optimized and disruption scenarios; persist results."""
    ensure_directories(cfg)
    output_dir = cfg.path("paths.optimization")
    metrics_path = output_dir / "scenario_results.json"
    if metrics_path.exists() and not force:
        logger.info("Optimization results exist; set force=True to rerun")
        return json.loads(metrics_path.read_text(encoding="utf-8"))

    log_section(logger, "Optimization: baseline fleet")
    base_instance = build_instance(cfg, scenario="baseline")
    aircraft_df, slot_df = instance_frames(base_instance)
    aircraft_df.to_csv(output_dir / "instance_aircraft.csv", index=False)
    slot_df.to_csv(output_dir / "instance_slots.csv", index=False)
    logger.info(
        "Instance: %d aircraft needing maintenance, %d slots, horizon=%d days",
        len(base_instance.aircraft),
        len(base_instance.slots),
        base_instance.horizon_days,
    )

    defer = defer_all_plan(base_instance)
    greedy = greedy_plan(base_instance, strategy="earliest")
    optimized = solve_maintenance_plan(base_instance, cfg)

    plan_frame(base_instance, optimized).to_csv(output_dir / "maintenance_plan_optimized.csv", index=False)
    plan_frame(base_instance, greedy).to_csv(output_dir / "maintenance_plan_greedy.csv", index=False)

    scenario_records: list[dict[str, Any]] = [
        _plan_record("Defer all (no maintenance)", defer),
        _plan_record("Current practice: earliest feasible slot", greedy),
        _plan_record("CP-SAT optimized", optimized),
    ]

    extra_scenarios = ["hangar_closure", "rul_stress", "demand_surge"]
    for scenario in extra_scenarios:
        instance = build_instance(cfg, scenario=scenario)
        optimized_scenario = solve_maintenance_plan(instance, cfg)
        greedy_scenario = greedy_plan(instance, strategy="earliest")
        record = _plan_record(f"Disruption: {scenario}", optimized_scenario)
        record["greedy_cost_usd"] = round(greedy_scenario.total_cost_usd, 0)
        record["greedy_unassigned"] = greedy_scenario.unassigned_count
        record["savings_vs_greedy_usd"] = round(
            greedy_scenario.total_cost_usd - optimized_scenario.total_cost_usd, 0
        )
        scenario_records.append(record)
        plan_frame(instance, optimized_scenario).to_csv(
            output_dir / f"maintenance_plan_{scenario}.csv", index=False
        )

    # Sensitivity to the AOG-risk penalty: a key policy lever for CAMO.
    sensitivity_rows = []
    for penalty in cfg.get("optimization.sensitivity_penalties", [15000, 30000, 45000, 60000]):
        stressed = copy.deepcopy(base_instance)
        stressed.cost_params["aog_risk_penalty_usd_per_day"] = float(penalty)
        plan = solve_maintenance_plan(stressed, cfg)
        sensitivity_rows.append(
            {
                "aog_risk_penalty_usd_per_day": float(penalty),
                "total_cost_usd": round(plan.total_cost_usd, 0),
                "assigned": plan.assigned_count,
                "unassigned": plan.unassigned_count,
                "aog_risk_usd": round(plan.breakdown.get("aog_risk_usd", 0.0), 0),
                "early_maintenance_usd": round(plan.breakdown.get("early_maintenance_usd", 0.0), 0),
            }
        )
    sensitivity = pd.DataFrame(sensitivity_rows)
    sensitivity.to_csv(output_dir / "sensitivity_aog_penalty.csv", index=False)

    # Figures.
    figures = cfg.path("paths.figures")
    fig, axis = plt.subplots(figsize=(9.2, 4.2))
    labels = ["Defer all", "Current practice", "CP-SAT optimized"]
    values = [defer.total_cost_usd / 1e6, greedy.total_cost_usd / 1e6, optimized.total_cost_usd / 1e6]
    bars = axis.bar(labels, values, color=["tab:red", "tab:gray", "tab:green"], alpha=0.85)
    axis.set_ylabel("Total operational disruption cost (USD millions)")
    axis.set_title("Maintenance plan cost: baseline vs optimized")
    for bar, value in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"${value:.2f}M", ha="center", va="bottom", fontsize=9)
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "optimization_cost_comparison.png", dpi=160)
    plt.close(fig)

    _gantt(base_instance, optimized, figures / "optimization_gantt.png")

    fig, axis = plt.subplots(figsize=(8.0, 4.0))
    axis.plot(sensitivity["aog_risk_penalty_usd_per_day"], sensitivity["total_cost_usd"] / 1e6, marker="o")
    axis.set_xlabel("AOG-risk penalty (USD per aircraft-day)")
    axis.set_ylabel("Total plan cost (USD millions)")
    axis.set_title("Sensitivity: AOG-risk penalty")
    axis.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "optimization_sensitivity.png", dpi=160)
    plt.close(fig)

    savings = greedy.total_cost_usd - optimized.total_cost_usd
    payload = {
        "assumptions": {
            "seed": int(cfg.get("project.seed", 42)),
            "horizon_days": base_instance.horizon_days,
            "start_date": str(base_instance.start_date),
            "aircraft_needing_maintenance": len(base_instance.aircraft),
            "hangar_slots": len(base_instance.slots),
            "note": "Synthetic fleet; costs are planning assumptions, not audited actuals.",
        },
        "baseline_vs_optimized": {
            "defer_all_cost_usd": round(defer.total_cost_usd, 0),
            "greedy_cost_usd": round(greedy.total_cost_usd, 0),
            "optimized_cost_usd": round(optimized.total_cost_usd, 0),
            "savings_vs_greedy_usd": round(savings, 0),
            "savings_vs_greedy_pct": round(100 * savings / max(greedy.total_cost_usd, 1), 2),
            "savings_vs_defer_usd": round(defer.total_cost_usd - optimized.total_cost_usd, 0),
            "greedy_unassigned": greedy.unassigned_count,
            "optimized_unassigned": optimized.unassigned_count,
            "solver_status": optimized.status,
            "solve_seconds": optimized.solve_seconds,
        },
        "scenarios": scenario_records,
        "sensitivity": sensitivity.to_dict(orient="records"),
        "breakdown_optimized": optimized.breakdown,
        "breakdown_greedy": greedy.breakdown,
    }
    write_json(payload, metrics_path)
    logger.info("Optimization scenarios written to %s", metrics_path)
    logger.info(
        "Greedy=%.0f Optimized=%.0f Savings=%.0f (%.1f%%)",
        greedy.total_cost_usd,
        optimized.total_cost_usd,
        savings,
        100 * savings / max(greedy.total_cost_usd, 1),
    )
    return payload


def load_scenario_results(cfg: Config) -> dict[str, Any]:
    path = cfg.path("paths.optimization") / "scenario_results.json"
    if not path.exists():
        return run_optimization_scenarios(cfg)
    return json.loads(path.read_text(encoding="utf-8"))
