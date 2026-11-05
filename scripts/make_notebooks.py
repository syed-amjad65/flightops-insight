"""Generate the executable Python notebooks with nbformat.

Run this script once, then execute the notebooks with::

    python -m jupyter nbconvert --to notebook --execute --inplace notebooks/01_otp_eda.ipynb
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = ROOT / "notebooks"

BOOTSTRAP = """\
import sys
from pathlib import Path

ROOT = Path.cwd()
if not (ROOT / "src").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
print("Repository root:", ROOT)
"""


def _write(name: str, cells: list[nbf.NotebookNode]) -> None:
    notebook = nbf.v4.new_notebook()
    notebook["cells"] = cells
    notebook["metadata"] = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
    }
    path = NOTEBOOKS / name
    nbf.write(notebook, path)
    print("wrote", path)


def otp_notebook() -> list[nbf.NotebookNode]:
    return [
        nbf.v4.new_markdown_cell(
            """# 01 - OTP EDA and model review

This notebook reviews the BTS 2024 on-time performance extract and the trained
LightGBM delay-risk model. It reads the processed Parquet/DuckDB artifacts; it does
not retrain the model, so it runs in seconds.

**Cutoff discipline.** The model scores a flight 90 minutes before scheduled
departure. Inbound actuals are only used when they were observable by that cutoff
(`inbound_known = 1`). Test months (Nov-Dec) are strictly after training and
validation months.
"""
        ),
        nbf.v4.new_code_cell(BOOTSTRAP),
        nbf.v4.new_code_cell(
            """\
import json
import duckdb
import pandas as pd
import matplotlib.pyplot as plt

from flightops.config import load_config

cfg = load_config()
con = duckdb.connect(str(cfg.path("data.otp.duckdb")), read_only=True)
metrics = json.loads((cfg.path("paths.metrics") / "otp_metrics.json").read_text())
insights = json.loads((cfg.path("paths.metrics") / "otp_insights.json").read_text())
overview = con.execute("SELECT * FROM otp_overview").fetchdf().iloc[0]
overview.to_dict()
"""
        ),
        nbf.v4.new_markdown_cell("## 1. Dataset and provenance"),
        nbf.v4.new_code_cell(
            """\
print(f"Flights: {int(overview['flights']):,}")
print(f"Delay rate (>15 min): {overview['delay_rate']:.1%}")
print(f"OTP rate: {overview['otp_rate']:.1%}")
print(f"Average departure delay: {overview['avg_dep_delay_min']:.1f} min")
print(f"Cancellation rate: {overview['cancellation_rate']:.1%}")
print("\\nSource: BTS On-Time Performance, 2024, all 12 months.")
print("Aircraft type is a synthetic enrichment because BTS has no equipment field.")
"""
        ),
        nbf.v4.new_markdown_cell("## 2. Where delay risk concentrates"),
        nbf.v4.new_code_cell(
            """\
monthly = con.execute("SELECT * FROM otp_monthly ORDER BY month").fetchdf()
hourly = con.execute("SELECT * FROM otp_hour ORDER BY dep_hour").fetchdf()
turn = con.execute("SELECT * FROM turnaround_effect").fetchdf()

fig, axes = plt.subplots(1, 3, figsize=(16, 4))
axes[0].bar(monthly["month"], monthly["delay_rate"], color="#7c3aed")
axes[0].set(title="Delay rate by month", xlabel="Month", ylabel="Share >15 min late")
axes[0].yaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
axes[0].grid(axis="y", alpha=0.25)

axes[1].plot(hourly["dep_hour"], hourly["delay_rate"], marker="o", color="#b91c1c")
axes[1].set(title="Delay rate by departure hour", xlabel="Scheduled hour", ylabel="Share >15 min late")
axes[1].yaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
axes[1].grid(alpha=0.25)
axes[1].axvspan(15, 20, color="#fecaca", alpha=0.4, label="15:00-20:00")
axes[1].legend()

order = ["< 40 min", "40-59 min", "60-89 min", "90-149 min", "150+ min", "No inbound link"]
turn["scheduled_turn_bucket"] = pd.Categorical(turn["scheduled_turn_bucket"], order, ordered=True)
turn = turn.sort_values("scheduled_turn_bucket")
axes[2].barh(turn["scheduled_turn_bucket"].astype(str), turn["delay_rate"], color="#0f766e")
axes[2].set(title="Delay rate by scheduled turnaround", xlabel="Share >15 min late")
axes[2].xaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
axes[2].invert_yaxis()
axes[2].grid(axis="x", alpha=0.25)
plt.tight_layout()
plt.show()
"""
        ),
        nbf.v4.new_code_cell(
            """\
carriers = con.execute("SELECT * FROM otp_carrier ORDER BY delay_rate DESC").fetchdf()
routes = con.execute("SELECT * FROM otp_route ORDER BY delay_rate DESC LIMIT 15").fetchdf()
causes = con.execute("SELECT * FROM delay_cause_mix ORDER BY delay_minutes DESC").fetchdf()

fig, axes = plt.subplots(1, 3, figsize=(16, 4))
axes[0].barh(carriers["carrier"], carriers["delay_rate"], color="#1d4ed8")
axes[0].set(title="Carrier spread", xlabel="Delay rate")
axes[0].xaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
axes[0].invert_yaxis()

axes[1].barh(routes["route"].astype(str), routes["delay_rate"], color="#dc2626")
axes[1].set(title="Highest-delay high-volume routes", xlabel="Delay rate")
axes[1].xaxis.set_major_formatter(lambda x, _: f"{x:.0%}")
axes[1].invert_yaxis()

axes[2].barh(causes["cause"].astype(str), causes["delay_minutes"] / 1e6, color="#a16207")
axes[2].set(title="Attributed delay minutes (millions)", xlabel="Delay minutes (M)")
axes[2].invert_yaxis()
plt.tight_layout()
plt.show()
"""
        ),
        nbf.v4.new_markdown_cell(
            """**Reading.** Delay risk climbs through the operating day and is highest on
short scheduled turnarounds. Carrier and route spreads are material, but these are
associations in observational data. Delay-cause fields are post-flight attributions
and are excluded from the model features.
"""
        ),
        nbf.v4.new_markdown_cell("## 3. Model performance on the held-out period"),
        nbf.v4.new_code_cell(
            """\
rows = []
for name in ["route_hour_baseline", "logistic_regression", "lightgbm"]:
    m = metrics["models"][name]
    rows.append({
        "model": name,
        "roc_auc": m["roc_auc"],
        "pr_auc": m["pr_auc"],
        "brier": m["brier"],
        "ece": m["ece"],
        "precision@0.5": m["precision"],
        "recall@0.5": m["recall"],
        "top_decile_lift": m["top_decile_lift"],
    })
comparison = pd.DataFrame(rows)
comparison.style.format({
    "roc_auc": "{:.3f}", "pr_auc": "{:.3f}", "brier": "{:.3f}", "ece": "{:.3f}",
    "precision@0.5": "{:.3f}", "recall@0.5": "{:.3f}", "top_decile_lift": "{:.2f}x",
})
"""
        ),
        nbf.v4.new_code_cell(
            """\
print("Operating threshold:", metrics["operating_threshold"]["recommended"])
print("Cost-optimal threshold (validation):", metrics["operating_threshold"]["selected"])
print("Best LightGBM iteration:", metrics["best_iteration"])
print("Top-decile capture:", f"{metrics['business_case_top_decile']['recall']:.1%}")
print("Top-decile precision:", f"{metrics['business_case_top_decile']['precision']:.1%}")
print("Top-decile flag rate:", f"{metrics['business_case_top_decile']['flag_rate']:.1%}")
"""
        ),
        nbf.v4.new_code_cell(
            """\
from IPython.display import Image, display

for name in ["otp_calibration.png", "otp_roc_pr.png", "otp_shap_bar.png"]:
    path = cfg.path("paths.figures") / name
    if path.exists():
        display(Image(filename=str(path), width=760))
"""
        ),
        nbf.v4.new_markdown_cell("## 4. Plain-language root cause"),
        nbf.v4.new_code_cell(
            """\
for item in insights["narrative"]:
    print("-", item)
"""
        ),
        nbf.v4.new_markdown_cell(
            """## 5. Limitations

- Observational associations, not causal effects.
- Aircraft type is a deterministic synthetic enrichment; validate it against a
  real fleet registry before operational use.
- Weather is represented only by season/calendar proxies.
- The business case assumes a prevention rate; it is not a measured treatment effect.
"""
        ),
    ]


def rul_notebook() -> list[nbf.NotebookNode]:
    return [
        nbf.v4.new_markdown_cell(
            """# 02 - C-MAPSS RUL modelling review

This notebook reviews the NASA C-MAPSS FD001 remaining-useful-life models: a Ridge
last-cycle baseline, an XGBoost window-summary model and a PyTorch LSTM. It reads
the saved metrics and watchlist; it does not retrain.
"""
        ),
        nbf.v4.new_code_cell(BOOTSTRAP),
        nbf.v4.new_code_cell(
            """\
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from flightops.config import load_config

cfg = load_config()
metrics = json.loads((cfg.path("paths.metrics") / "rul_metrics.json").read_text())
watch = pd.read_csv(cfg.path("paths.metrics") / "rul_watchlist.csv")
cost = pd.read_csv(cfg.path("paths.metrics") / "rul_alert_cost.csv")
cmapss = pd.read_parquet(cfg.path("data.rul.parquet"))
print("Test engines:", len(watch))
print("Models:", ", ".join(metrics["models"]))
"""
        ),
        nbf.v4.new_markdown_cell("## 1. Model comparison"),
        nbf.v4.new_code_cell(
            """\
rows = []
for name, m in metrics["models"].items():
    rows.append({"model": name, **{k: m[k] for k in ["rmse", "mae", "r2", "nasa_score_mean", "late_prediction_rate", "p90_abs_error"]}})
table = pd.DataFrame(rows)
table.style.format({"rmse": "{:.2f}", "mae": "{:.2f}", "r2": "{:.3f}", "nasa_score_mean": "{:.2f}", "late_prediction_rate": "{:.0%}", "p90_abs_error": "{:.1f}"})
"""
        ),
        nbf.v4.new_code_cell(
            """\
print(f"Conformal 90% interval: +/-{metrics['conformal']['q_cycles']:.1f} cycles")
print(f"Empirical test coverage: {metrics['conformal']['test_coverage']:.1%}")
print(f"Selected alert threshold: {metrics['alerts']['selected_threshold_cycles']} cycles")
print(f"Cost-optimal threshold: {metrics['alerts']['cost_optimal_threshold_cycles']} cycles")
print(f"Selected precision/recall: {metrics['alerts']['selected']['precision']:.1%} / {metrics['alerts']['selected']['recall']:.1%}")
"""
        ),
        nbf.v4.new_markdown_cell("## 2. Prediction quality and asymmetry"),
        nbf.v4.new_code_cell(
            """\
fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
axes[0].scatter(watch["true_rul_cycles"], watch["predicted_rul_cycles"], c=np.abs(watch["predicted_rul_cycles"] - watch["true_rul_cycles"]), cmap="viridis", s=26)
lim = max(watch["true_rul_cycles"].max(), watch["predicted_rul_cycles"].max()) * 1.05
axes[0].plot([0, lim], [0, lim], "r--", linewidth=1, label="Perfect")
axes[0].set(title="Predicted vs true RUL (test engines)", xlabel="True RUL (cycles)", ylabel="Predicted RUL (cycles)")
axes[0].legend()
axes[0].grid(alpha=0.25)

errors = watch["predicted_rul_cycles"] - watch["true_rul_cycles"]
axes[1].hist(errors, bins=22, color="#0f766e", alpha=0.85)
axes[1].axvline(0, color="black", linestyle="--", linewidth=1)
axes[1].set(title="LSTM error (predicted - true)", xlabel="Cycles (positive = late prediction)", ylabel="Engines")
axes[1].grid(axis="y", alpha=0.25)
plt.tight_layout()
plt.show()
print(f"Late-prediction rate: {(errors > 0).mean():.1%}; mean error {errors.mean():+.2f} cycles")
"""
        ),
        nbf.v4.new_markdown_cell("## 3. Alert threshold economics"),
        nbf.v4.new_code_cell(
            """\
fig, ax = plt.subplots(figsize=(8.5, 4.2))
ax.plot(cost["threshold_cycles"], cost["total_cost_usd"] / 1e6, color="#334155", marker="o", markersize=3)
ax.axvline(metrics["alerts"]["selected_threshold_cycles"], color="#b91c1c", linestyle="--", label="Selected")
ax.axvline(metrics["alerts"]["cost_optimal_threshold_cycles"], color="#0f766e", linestyle=":", label="Cost-optimal")
ax.set(title="RUL alert threshold: early removal vs unscheduled removal", xlabel="Alert threshold (cycles)", ylabel="Expected cost (USD millions)")
ax.legend()
ax.grid(alpha=0.25)
plt.tight_layout()
plt.show()

print(f"Cost at selected threshold: ${metrics['alerts']['selected']['total_cost_usd']/1e6:.2f}M")
print(f"No-alert benchmark: ${metrics['alerts']['benchmark_no_alert_cost_usd']/1e6:.2f}M")
print(f"Remove-all-early benchmark: ${metrics['alerts']['benchmark_remove_all_early_cost_usd']/1e6:.2f}M")
"""
        ),
        nbf.v4.new_markdown_cell("## 4. Watchlist and degradation trajectories"),
        nbf.v4.new_code_cell(
            """\
tiers = watch["risk_tier"].value_counts()
print(tiers.to_string())
watch.sort_values("predicted_rul_cycles").head(12)[["unit", "predicted_rul_cycles", "prediction_low", "prediction_high", "true_rul_cycles", "risk_tier"]]
"""
        ),
        nbf.v4.new_code_cell(
            """\
units = watch.sort_values("predicted_rul_cycles").head(4)["unit"].astype(int).tolist()
fig, axes = plt.subplots(2, 2, figsize=(12, 6), sharex=False)
for ax, unit in zip(axes.ravel(), units):
    frame = cmapss[(cmapss["split"] == "test") & (cmapss["unit"] == unit)]
    ax.plot(frame["cycle"], frame["sensor_11"], color="#0f172a")
    ax.set(title=f"Engine {unit}: sensor_11", xlabel="Cycle", ylabel="Sensor value")
    ax.grid(alpha=0.25)
plt.tight_layout()
plt.show()
"""
        ),
        nbf.v4.new_markdown_cell(
            """## 5. Limitations

- C-MAPSS is simulated turbofan degradation, not a fleet feed.
- The event is an RUL threshold, not a physical failure; cost figures are planning
  assumptions.
- The model is a decision aid. In service it must be combined with borescope, oil
  analysis and OEM limits, and signed off by a licensed engineer.
"""
        ),
    ]


def optimization_notebook() -> list[nbf.NotebookNode]:
    return [
        nbf.v4.new_markdown_cell(
            """# 03 - Maintenance optimization and scenarios

This notebook reviews the CP-SAT aircraft-to-hangar plan on the synthetic fleet
instance. It also re-validates the saved plan against the hard constraints, so the
schedule is auditable rather than just a chart.
"""
        ),
        nbf.v4.new_code_cell(BOOTSTRAP),
        nbf.v4.new_code_cell(
            """\
import json
import pandas as pd
import matplotlib.pyplot as plt

from flightops.config import load_config
from flightops.optimization.maintenance_planner import MaintenancePlan, evaluate_plan, validate_plan
from flightops.optimization.synthetic_fleet import build_instance

cfg = load_config()
results = json.loads((cfg.path("paths.optimization") / "scenario_results.json").read_text())
aircraft = pd.read_csv(cfg.path("paths.optimization") / "instance_aircraft.csv")
plan_df = pd.read_csv(cfg.path("paths.optimization") / "maintenance_plan_optimized.csv")
slots = pd.read_csv(cfg.path("paths.optimization") / "instance_slots.csv")
print(f"Aircraft: {len(aircraft)}, slots: {len(slots)}, horizon: {results['assumptions']['horizon_days']} days")
"""
        ),
        nbf.v4.new_markdown_cell("## 1. Baseline vs optimized cost"),
        nbf.v4.new_code_cell(
            """\
base = results["baseline_vs_optimized"]
labels = ["Defer all", "Current practice", "CP-SAT optimized"]
values = [base["defer_all_cost_usd"], base["greedy_cost_usd"], base["optimized_cost_usd"]]
fig, ax = plt.subplots(figsize=(8.5, 4.2))
bars = ax.bar(labels, [v / 1e6 for v in values], color=["#b91c1c", "#94a3b8", "#0f766e"])
for bar, value in zip(bars, values):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(), f"${value/1e6:.2f}M", ha="center", va="bottom")
ax.set(title="Maintenance plan cost", ylabel="USD millions")
ax.grid(axis="y", alpha=0.25)
plt.tight_layout()
plt.show()
print(f"Saving vs current practice: ${base['savings_vs_greedy_usd']:,.0f} ({base['savings_vs_greedy_pct']:.1f}%)")
print(f"Solver: {base['solver_status']} in {base['solve_seconds']:.2f}s; unassigned {base['greedy_unassigned']} -> {base['optimized_unassigned']}")
"""
        ),
        nbf.v4.new_markdown_cell("## 2. Schedule (Gantt)"),
        nbf.v4.new_code_cell(
            """\
hangars = sorted(slots["hangar"].unique())
fig, ax = plt.subplots(figsize=(11, 4.4))
colors = {"A": "#1d4ed8", "C": "#ea580c"}
for row in plan_df.itertuples():
    slot = slots[slots["slot_id"] == row.assigned_slot].iloc[0]
    y = hangars.index(slot["hangar"])
    ax.barh(y, slot["duration_days"], left=slot["start_day"], height=0.55, color=colors[slot["check_type"]], edgecolor="black", linewidth=0.4)
    ax.text(slot["start_day"] + slot["duration_days"]/2, y, row.tail, ha="center", va="center", fontsize=7, color="white" if slot["check_type"] == "A" else "black")
ax.set_yticks(range(len(hangars)))
ax.set_yticklabels(hangars)
ax.set(title="Optimized hangar schedule (A=blue, C=orange)", xlabel="Planning day", ylabel="Hangar")
ax.set_xlim(0, results["assumptions"]["horizon_days"])
ax.grid(axis="x", alpha=0.25)
plt.tight_layout()
plt.show()
"""
        ),
        nbf.v4.new_markdown_cell("## 3. Constraint audit"),
        nbf.v4.new_code_cell(
            """\
instance = build_instance(cfg, scenario="baseline")
assignments = dict(zip(plan_df["tail"], plan_df["assigned_slot"]))
saved = MaintenancePlan(assignments=assignments, unassigned=[])
errors = validate_plan(instance, saved)
recomputed = evaluate_plan(instance, assignments)
print("Constraint violations:", errors if errors else "none")
print(f"Recomputed cost: ${recomputed.total_cost_usd:,.0f}")
print(f"Saved cost:      ${plan_df['total_cost_usd'].sum():,.0f}")
print("Breakdown:", recomputed.breakdown)
"""
        ),
        nbf.v4.new_markdown_cell("## 4. Disruption scenarios and sensitivity"),
        nbf.v4.new_code_cell(
            """\
scenarios = pd.DataFrame(results["scenarios"])
scenarios["uplift_vs_baseline_usd"] = scenarios["total_cost_usd"] - base["optimized_cost_usd"]
scenarios[["scenario", "assigned", "unassigned", "total_cost_usd", "uplift_vs_baseline_usd", "greedy_cost_usd", "savings_vs_greedy_usd"]]
"""
        ),
        nbf.v4.new_code_cell(
            """\
sens = pd.DataFrame(results["sensitivity"])
fig, ax = plt.subplots(figsize=(8.5, 4.0))
ax.plot(sens["aog_risk_penalty_usd_per_day"], sens["total_cost_usd"] / 1e6, marker="o", color="#0f766e")
ax.set(title="Sensitivity: AOG-risk penalty", xlabel="AOG-risk penalty (USD per aircraft-day)", ylabel="Plan cost (USD millions)")
ax.grid(alpha=0.25)
plt.tight_layout()
plt.show()
sens
"""
        ),
        nbf.v4.new_markdown_cell(
            """## 5. Limitations

- The fleet, slots and costs are synthetic and deterministic from the project seed.
- The model assumes one aircraft position per hangar-day and pre-computed costs.
- A real deployment needs live CAMO data, crew constraints, parts availability and
  an approved maintenance programme. The solver is a planning aid, not a
  release-to-service authority.
"""
        ),
    ]


def main() -> None:
    NOTEBOOKS.mkdir(parents=True, exist_ok=True)
    _write("01_otp_eda.ipynb", otp_notebook())
    _write("02_rul_modeling.ipynb", rul_notebook())
    _write("03_optimization_scenarios.ipynb", optimization_notebook())


if __name__ == "__main__":
    main()
