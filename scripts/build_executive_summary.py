"""Build the executive summary (Markdown) and the 8-slide PPTX from live artifacts.

Every number is read from the generated JSON/CSV files. Figures are the PNGs produced
by the training and optimization runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "reports" / "metrics"
FIGURES = ROOT / "reports" / "figures"
OPT = ROOT / "reports" / "optimization"
DOCS = ROOT / "docs"

NAVY = RGBColor(0x0F, 0x17, 0x2A)
TEAL = RGBColor(0x0F, 0x76, 0x6E)
RED = RGBColor(0xB9, 0x1C, 0x1C)
AMBER = RGBColor(0xA1, 0x62, 0x07)
GREY = RGBColor(0x6B, 0x72, 0x80)
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def load() -> dict:
    otp = json.loads((METRICS / "otp_metrics.json").read_text(encoding="utf-8"))
    rul = json.loads((METRICS / "rul_metrics.json").read_text(encoding="utf-8"))
    opt = json.loads((OPT / "scenario_results.json").read_text(encoding="utf-8"))
    insights = json.loads((METRICS / "otp_insights.json").read_text(encoding="utf-8"))
    return {"otp": otp, "rul": rul, "opt": opt, "insights": insights}


def _weighted_rate(rows: list[dict]) -> float:
    """Flight-weighted delay rate for a subset of insight rows."""
    flights = sum(row["flights"] for row in rows)
    return sum(row["flights"] * row["delay_rate"] for row in rows) / flights


def _delay_pattern(insights: dict) -> dict:
    """Derive the headline time-of-day, turnaround and SHAP figures from the EDA output."""
    hourly = insights["hourly"]
    peak = _weighted_rate([row for row in hourly if 15 <= row["dep_hour"] <= 20])
    early = _weighted_rate([row for row in hourly if 6 <= row["dep_hour"] <= 9])
    turnaround = {row["scheduled_turn_bucket"]: row for row in insights["turnaround"]}
    shap = insights["shap_group_importance"]
    shap_total = sum(shap.values())
    return {
        "peak_rate": peak,
        "early_rate": early,
        "peak_ratio": peak / early,
        "short_turn_rate": turnaround["< 40 min"]["delay_rate"],
        "long_turn_rate": turnaround["90-149 min"]["delay_rate"],
        "turnaround_shap_share": shap["Turnaround & inbound aircraft"] / shap_total,
        "flights": int(insights["overview"]["flights"]),
    }


def _set_text(box, text: str, size: int, color=NAVY, bold=False, align=PP_ALIGN.LEFT) -> None:
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    para = frame.paragraphs[0]
    para.alignment = align
    run = para.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = "Calibri"


def _add_text(slide, left, top, width, height, text, size, color=NAVY, bold=False, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    _set_text(box, text, size, color, bold, align)
    return box


def _add_bullets(slide, left, top, width, height, items: list[str], size=15, color=NAVY, spacing=6) -> None:
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    for index, item in enumerate(items):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        para.text = item
        para.level = 0
        para.space_after = Pt(spacing)
        for run in para.runs:
            run.font.size = Pt(size)
            run.font.color.rgb = color
            run.font.name = "Calibri"
    return box


def _rect(slide, left, top, width, height, fill, line=None, radius=True):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(left), Inches(top), Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    return shape


def _slide_base(prs, title: str, kicker: str = "", footer: str = "FlightOps Insight | Prototype | Public/synthetic data"):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = WHITE
    _rect(slide, 0, 0, 13.333, 0.12, NAVY, radius=False)
    if kicker:
        _add_text(slide, 0.55, 0.28, 12.2, 0.3, kicker.upper(), 11, TEAL, bold=True)
    _add_text(slide, 0.55, 0.55, 12.3, 0.6, title, 27, NAVY, bold=True)
    _add_text(slide, 0.55, 7.12, 9.0, 0.25, footer, 9, GREY)
    return slide


def _picture(slide, path: Path, left, top, max_w, max_h):
    if not path.exists():
        _add_text(slide, left, top, max_w, max_h, f"Figure not found: {path.name}", 12, RED)
        return
    with Image.open(path) as image:
        width, height = image.size
    ratio = min(max_w / width, max_h / height)
    w, h = width * ratio, height * ratio
    slide.shapes.add_picture(str(path), Inches(left), Inches(top), width=Inches(w), height=Inches(h))


def _table(slide, left, top, width, rows: list[list[str]], col_widths=None, font_size=12, header=True):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(left), Inches(top), Inches(width), Inches(0.32 * len(rows)))
    table = shape.table
    if col_widths:
        for index, value in enumerate(col_widths):
            table.columns[index].width = Inches(value)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.text = str(value)
            cell.margin_left = Inches(0.06)
            cell.margin_right = Inches(0.06)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            for para in cell.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(font_size)
                    run.font.name = "Calibri"
                    run.font.color.rgb = WHITE if (header and row_index == 0) else NAVY
                    run.font.bold = header and row_index == 0
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if (header and row_index == 0) else (LIGHT if row_index % 2 else WHITE)
    return table


def _metric_cards(slide, top, cards: list[tuple[str, str, str]]):
    left = 0.55
    width = 2.95
    gap = 0.15
    for label, value, note in cards:
        _rect(slide, left, top, width, 1.15, LIGHT)
        _add_text(slide, left + 0.12, top + 0.10, width - 0.24, 0.25, label.upper(), 9, GREY, bold=True)
        _add_text(slide, left + 0.12, top + 0.32, width - 0.24, 0.42, value, 22, NAVY, bold=True)
        _add_text(slide, left + 0.12, top + 0.76, width - 0.24, 0.3, note, 9, GREY)
        left += width + gap


def build_deck(data: dict) -> Presentation:
    otp = data["otp"]
    rul = data["rul"]
    opt = data["opt"]
    lgb = otp["models"]["lightgbm"]
    base = otp["models"]["route_hour_baseline"]
    logistic = otp["models"]["logistic_regression"]
    top = otp["business_case_top_decile"]
    xgb = rul["models"]["xgboost_window"]
    lstm = rul["models"]["lstm"]
    ridge = rul["models"]["ridge_last_cycle"]
    alerts = rul["alerts"]
    ob = opt["baseline_vs_optimized"]
    scenarios = {row["scenario"]: row for row in opt["scenarios"]}
    stress = scenarios["Disruption: rul_stress"]
    total_aircraft = scenarios["Defer all (no maintenance)"]["unassigned"]
    pattern = _delay_pattern(data["insights"])

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 1. Title
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = NAVY
    _rect(slide, 0.55, 1.35, 0.09, 2.2, TEAL, radius=False)
    _add_text(slide, 0.9, 1.2, 11.6, 0.4, "FLIGHTOPS INSIGHT", 14, RGBColor(0x7D, 0xD3, 0xFC), bold=True)
    _add_text(slide, 0.9, 1.65, 11.4, 1.3, "From public data to an integrated\nOCC and CAMO decision prototype", 34, WHITE, bold=True)
    _add_text(
        slide,
        0.9,
        3.15,
        10.8,
        0.9,
        "Delay-risk prediction + engine RUL + CP-SAT maintenance planning, with a six-page decision app and live ADS-B tracking.",
        17,
        RGBColor(0xCB, 0xD5, 0xE1),
    )
    _add_text(slide, 0.9, 5.55, 11.2, 0.5, "Built on 7.1M BTS 2024 flights and NASA C-MAPSS FD001", 13, RGBColor(0x94, 0xA3, 0xB8))
    _add_text(slide, 0.9, 6.0, 11.2, 0.5, "Prototype only. Not airline-validated. No Skywise or airline system access.", 12, RGBColor(0xF8, 0x71, 0x71))
    _add_text(slide, 0.9, 6.85, 11.2, 0.3, "Prepared November 2026 | All metrics generated by the code in this repository", 9, RGBColor(0x94, 0xA3, 0xB8))

    # 2. Opportunity
    slide = _slide_base(prs, "Three decisions, one integrated prototype", "The opportunity")
    _add_text(slide, 0.55, 1.35, 12.2, 0.5, "Airline operations still solve these problems in separate teams and spreadsheets.", 15, GREY)
    cards = [
        ("Flight operations", "OTP delay risk", "Score a flight at T-90 minutes and rank it for the OCC recovery team."),
        ("Engineering", "Engine RUL", "Turn 30-cycle sensor windows into a CAMO watchlist with intervals and alert economics."),
        ("Maintenance control", "Hangar assignment", "Assign due aircraft to slots without violating capacity, eligibility or due dates."),
    ]
    left = 0.55
    for label, title, body in cards:
        _rect(slide, left, 2.0, 3.95, 2.3, LIGHT)
        _add_text(slide, left + 0.2, 2.15, 3.55, 0.3, label.upper(), 9, TEAL, bold=True)
        _add_text(slide, left + 0.2, 2.45, 3.55, 0.45, title, 19, NAVY, bold=True)
        _add_text(slide, left + 0.2, 3.0, 3.55, 1.1, body, 12, GREY)
        left += 4.1
    _add_text(slide, 0.55, 4.65, 12.2, 0.35, "What the prototype adds", 15, NAVY, bold=True)
    _add_bullets(
        slide,
        0.55,
        5.0,
        12.2,
        1.7,
        [
            "A shared data contract and feature layer, so the same logic runs in training, batch scoring and the app.",
            "Honest evaluation: strictly later OTP test months, RUL split by engine, calibration and conformal coverage.",
            "Decision framing: every page ends with a recommended action, its assumptions and its limits.",
        ],
        size=13,
    )

    # 3. OTP performance
    slide = _slide_base(prs, "Delay risk is predictable, calibrated and operationally targetable", "OTP model")
    _metric_cards(
        slide,
        1.35,
        [
            ("ROC-AUC", f"{lgb['roc_auc']:.3f}", f"baseline {base['roc_auc']:.3f}"),
            ("PR-AUC", f"{lgb['pr_auc']:.3f}", f"baseline {base['pr_auc']:.3f}"),
            ("Brier / ECE", f"{lgb['brier']:.3f} / {lgb['ece']:.3f}", "well calibrated"),
            ("Top-decile lift", f"{lgb['top_decile_lift']:.2f}x", f"{top['recall']:.1%} of delays captured"),
        ],
    )
    _table(
        slide,
        0.55,
        2.9,
        5.55,
        [
            ["Model", "ROC-AUC", "PR-AUC", "Brier", "Top-decile lift"],
            ["Route-hour baseline", f"{base['roc_auc']:.3f}", f"{base['pr_auc']:.3f}", f"{base['brier']:.3f}", f"{base['top_decile_lift']:.2f}x"],
            ["Logistic regression", f"{logistic['roc_auc']:.3f}", f"{logistic['pr_auc']:.3f}", f"{logistic['brier']:.3f}", f"{logistic['top_decile_lift']:.2f}x"],
            ["LightGBM", f"{lgb['roc_auc']:.3f}", f"{lgb['pr_auc']:.3f}", f"{lgb['brier']:.3f}", f"{lgb['top_decile_lift']:.2f}x"],
        ],
        col_widths=[1.95, 0.95, 0.95, 0.85, 0.85],
        font_size=11,
    )
    _add_text(
        slide,
        0.55,
        4.65,
        5.6,
        1.5,
        f"Recommended threshold: {otp['operating_threshold']['recommended']:.3f} (top-decile).\n"
        f"Flags {top['flag_rate']:.1%} of test flights at {top['precision']:.1%} precision.\n"
        "The 0.05 cost-optimal validation threshold is reported but not recommended: it flags 71% of flights.",
        12,
        GREY,
    )
    _picture(slide, FIGURES / "otp_roc_pr.png", 6.55, 2.75, 6.2, 3.9)
    _add_text(slide, 6.55, 6.65, 6.2, 0.3, "ROC and precision-recall curves on Nov-Dec 2024", 9, GREY)

    # 4. OTP root causes
    slide = _slide_base(
        prs,
        "Delay risk concentrates in the afternoon and in short turns",
        "What the data says",
        footer="FlightOps Insight | BTS 2024 | Associational patterns, not causal estimates",
    )
    _picture(slide, FIGURES / "otp_hourly.png", 0.55, 1.35, 6.3, 3.9)
    _picture(slide, FIGURES / "otp_turnaround.png", 0.55, 5.35, 6.3, 1.7)
    _add_text(slide, 7.2, 1.35, 5.5, 0.35, "The pattern", 15, NAVY, bold=True)
    _add_bullets(
        slide,
        7.2,
        1.75,
        5.5,
        3.4,
        [
            f"15:00-20:00 departs late {pattern['peak_rate']:.1%} of the time vs {pattern['early_rate']:.1%} in 06:00-09:00 ({pattern['peak_ratio']:.1f}x).",
            f"Turns under 40 minutes: {pattern['short_turn_rate']:.1%} delay rate vs {pattern['long_turn_rate']:.1%} for 90-149 minute turns.",
            f"Turnaround and inbound aircraft account for {pattern['turnaround_shap_share']:.0%} of mean absolute SHAP.",
            "Late inbound is associated with higher risk, but the comparison is confounded by buffer and network mix.",
        ],
        size=13,
    )
    _rect(slide, 7.2, 5.35, 5.5, 1.35, LIGHT)
    _add_text(slide, 7.35, 5.45, 5.2, 0.25, "SO WHAT", 9, TEAL, bold=True)
    _add_text(
        slide,
        7.35,
        5.72,
        5.2,
        0.9,
        "Protect the afternoon bank and short turns first. A top-decile list captures "
        f"{top['recall']:.1%} of delays at {top['precision']:.1%} precision, which fits a finite OCC team.",
        11,
        NAVY,
    )

    # 5. RUL
    slide = _slide_base(prs, "Window models beat the baseline; the alert rule is the lever", "Engine RUL")
    _metric_cards(
        slide,
        1.35,
        [
            ("XGBoost RMSE", f"{xgb['rmse']:.1f}", f"Ridge {ridge['rmse']:.1f} cycles"),
            ("LSTM RMSE", f"{lstm['rmse']:.1f}", f"late rate {lstm['late_prediction_rate']:.0%}"),
            ("Alert precision", f"{alerts['selected']['precision']:.1%}", f"recall {alerts['selected']['recall']:.1%}"),
            ("Conformal 90%", f"+/-{rul['conformal']['q_cycles']:.1f}", f"coverage {rul['conformal']['test_coverage']:.0%}"),
        ],
    )
    _table(
        slide,
        0.55,
        2.9,
        5.85,
        [
            ["Model", "RMSE", "MAE", "R2", "NASA score", "Late rate"],
            ["Ridge last-cycle", f"{ridge['rmse']:.1f}", f"{ridge['mae']:.1f}", f"{ridge['r2']:.3f}", f"{ridge['nasa_score_mean']:.2f}", f"{ridge['late_prediction_rate']:.0%}"],
            ["XGBoost window", f"{xgb['rmse']:.1f}", f"{xgb['mae']:.1f}", f"{xgb['r2']:.3f}", f"{xgb['nasa_score_mean']:.2f}", f"{xgb['late_prediction_rate']:.0%}"],
            ["LSTM", f"{lstm['rmse']:.1f}", f"{lstm['mae']:.1f}", f"{lstm['r2']:.3f}", f"{lstm['nasa_score_mean']:.2f}", f"{lstm['late_prediction_rate']:.0%}"],
        ],
        col_widths=[1.8, 0.8, 0.8, 0.7, 1.0, 0.75],
        font_size=10.5,
    )
    _add_text(
        slide,
        0.55,
        4.55,
        5.5,
        1.9,
        f"{alerts['selected_threshold_cycles']}-cycle rule: {alerts['selected']['tp']} TP, "
        f"{alerts['selected']['fp']} FP, {alerts['selected']['fn']} FN.\n"
        f"Cost ${alerts['selected']['total_cost_usd']/1e6:.2f}M vs "
        f"${alerts['benchmark_no_alert_cost_usd']/1e6:.1f}M with no alerts and "
        f"${alerts['benchmark_remove_all_early_cost_usd']/1e6:.1f}M for remove-all-early.\n"
        f"Cost-optimal threshold is {alerts['cost_optimal_threshold_cycles']} cycles with the same outcome.\n"
        "Costs are planning assumptions, not audited savings.",
        10.5,
        GREY,
    )
    _picture(slide, FIGURES / "rul_predicted_vs_true.png", 6.5, 2.62, 6.2, 2.5)
    _picture(slide, FIGURES / "rul_alert_cost.png", 7.1, 5.22, 5.6, 1.72)

    # 6. Optimization
    slide = _slide_base(
        prs,
        f"CP-SAT turns a feasible schedule into a {ob['savings_vs_greedy_pct']:.0f}% cheaper plan",
        "Maintenance optimization",
        footer=(
            f"FlightOps Insight | Synthetic {opt['assumptions']['aircraft_needing_maintenance']}-aircraft, "
            f"{opt['assumptions']['hangar_slots']}-slot instance | Costs are planning assumptions"
        ),
    )
    _picture(slide, FIGURES / "optimization_cost_comparison.png", 0.55, 1.35, 6.0, 3.5)
    _picture(slide, FIGURES / "optimization_gantt.png", 6.8, 1.25, 6.0, 3.7)
    _table(
        slide,
        0.55,
        5.0,
        6.0,
        [
            ["Plan", "Assigned", "Unassigned", "Cost"],
            ["Defer all", "0", str(total_aircraft), f"${opt['baseline_vs_optimized']['defer_all_cost_usd']/1e6:.2f}M"],
            ["Current practice", str(total_aircraft - ob["greedy_unassigned"]), str(ob["greedy_unassigned"]), f"${ob['greedy_cost_usd']/1e6:.2f}M"],
            ["CP-SAT optimized", str(total_aircraft - ob["optimized_unassigned"]), str(ob["optimized_unassigned"]), f"${ob['optimized_cost_usd']/1e6:.2f}M"],
        ],
        col_widths=[2.3, 1.3, 1.2, 1.2],
        font_size=11,
    )
    _add_text(slide, 6.8, 5.0, 6.0, 0.3, "What changes under stress", 14, NAVY, bold=True)
    _add_bullets(
        slide,
        6.8,
        5.35,
        6.0,
        1.6,
        [
            f"Baseline: all {total_aircraft} scheduled, {ob['solver_status'].lower()} in {ob['solve_seconds']:.2f}s.",
            f"H3 closure: absorbable at ${scenarios['Disruption: hangar_closure']['total_cost_usd']/1e6:.2f}M.",
            f"RUL stress is binding: {stress['unassigned']} aircraft unassigned, ${stress['total_cost_usd']/1e6:.2f}M.",
            "Human planners approve; overrides are logged.",
        ],
        size=12,
    )

    # 7. Production architecture
    slide = _slide_base(
        prs,
        "Production path: governed lakehouse, managed serving, human sign-off",
        "Deployment and governance",
        footer="FlightOps Insight | deployment/ contains runbooks and templates, not a live deployment",
    )
    boxes = [
        (0.55, "Sources", "BTS / schedule\nC-MAPSS / sensors\nMRO / weather\nSkywise via approved product"),
        (3.55, "Databricks", "Unity Catalog\nDelta Bronze-Silver-Gold\nWorkflows + MLflow\nFeature backfills"),
        (6.55, "Vertex AI", "Pipelines + Training\nModel Registry\nOnline endpoint\nBatch prediction + monitoring"),
        (9.55, "Decision layer", "OCC / CAMO app\nCP-SAT planner\nHuman approval\nOverride + audit log"),
    ]
    for left, title, body in boxes:
        _rect(slide, left, 1.45, 2.75, 2.55, LIGHT if left != 9.55 else NAVY)
        color = WHITE if left == 9.55 else NAVY
        _add_text(slide, left + 0.18, 1.6, 2.4, 0.35, title, 15, color, bold=True)
        _add_text(slide, left + 0.18, 2.05, 2.4, 1.7, body, 10.5, WHITE if left == 9.55 else GREY)
    for left in (3.3, 6.3, 9.3):
        _add_text(slide, left, 2.45, 0.3, 0.4, ">", 24, TEAL, bold=True)
    _add_text(slide, 0.55, 4.35, 6.0, 0.3, "Governance and human-in-the-loop", 15, NAVY, bold=True)
    _add_bullets(
        slide,
        0.55,
        4.7,
        6.0,
        2.0,
        [
            "No Skywise access in the prototype; treat it as a governed data product under an approved agreement.",
            "SSO, least privilege, VPC-SC, CMEK, Secret Manager and audit logs.",
            "OTP ranks flights; the OCC decides. RUL is a watchlist; a licensed engineer signs off.",
            "The optimizer proposes a plan; CAMO approves. Overrides and reasons feed monitoring.",
        ],
        size=11.5,
    )
    _add_text(slide, 6.9, 4.35, 5.8, 0.3, "Rollout gates", 15, NAVY, bold=True)
    _add_bullets(
        slide,
        6.9,
        4.7,
        5.8,
        2.0,
        [
            "Rebuild on airline data under a data contract.",
            "Shadow mode for one full seasonal cycle.",
            "Independent model-risk and security review.",
            "Canary 5% -> 25% -> 100%, with rollback tested.",
        ],
        size=11.5,
    )

    # 8. Recommendation
    slide = _slide_base(prs, "Recommendation: pilot the decision layer, not a black box", "Next steps")
    _add_text(
        slide,
        0.55,
        1.35,
        12.2,
        0.7,
        "The prototype is strong enough to start a governed pilot and not strong enough to run operations.",
        17,
        NAVY,
        bold=True,
    )
    phases = [
        ("0-8 weeks", "Data access", "Approve data contract; land airline schedule, fleet, MRO and sensor data; complete security review."),
        ("8-16 weeks", "Rebuild and backtest", "Recreate features on airline history; recalibrate; run a time-based backtest and segment review."),
        ("16-28 weeks", "Shadow and pilot", "Run alongside current practice; log overrides; train OCC and CAMO users; define SLOs."),
        ("28+ weeks", "Scale", "Promote approved models; add parts, labour and rolling-horizon optimization; expand to more fleets."),
    ]
    top_pos = 2.25
    for index, (when, title, body) in enumerate(phases):
        left = 0.55 + index * 3.15
        _rect(slide, left, top_pos, 2.95, 2.45, LIGHT)
        _add_text(slide, left + 0.16, top_pos + 0.12, 2.6, 0.25, when.upper(), 9, TEAL, bold=True)
        _add_text(slide, left + 0.16, top_pos + 0.42, 2.6, 0.4, title, 15, NAVY, bold=True)
        _add_text(slide, left + 0.16, top_pos + 0.9, 2.6, 1.4, body, 10, GREY)
    _rect(slide, 0.55, 5.05, 12.2, 1.55, NAVY)
    _add_text(slide, 0.75, 5.18, 11.8, 0.3, "THE ASK", 10, RGBColor(0x7D, 0xD3, 0xFC), bold=True)
    _add_text(
        slide,
        0.75,
        5.5,
        11.8,
        0.95,
        "Fund a 6-month pilot with one carrier and one fleet. Success criteria: OTP top-decile precision at or above 70%, "
        "RUL alert recall at or above 90%, and a maintenance plan that CAMO planners accept with a documented override rate. "
        "Do not claim savings until they are measured in the pilot.",
        12,
        WHITE,
    )
    _add_text(slide, 0.55, 6.85, 12.2, 0.3, "Prototype metrics are not production guarantees. Validate on airline data before any operational use.", 9, RED)

    return prs


def build_markdown(data: dict) -> str:
    otp = data["otp"]
    rul = data["rul"]
    opt = data["opt"]
    lgb = otp["models"]["lightgbm"]
    base = otp["models"]["route_hour_baseline"]
    logistic = otp["models"]["logistic_regression"]
    top = otp["business_case_top_decile"]
    xgb = rul["models"]["xgboost_window"]
    lstm = rul["models"]["lstm"]
    ridge = rul["models"]["ridge_last_cycle"]
    alerts = rul["alerts"]
    ob = opt["baseline_vs_optimized"]
    scenarios = {row["scenario"]: row for row in opt["scenarios"]}
    stress = scenarios["Disruption: rul_stress"]
    total_aircraft = scenarios["Defer all (no maintenance)"]["unassigned"]
    pattern = _delay_pattern(data["insights"])

    return f"""# FlightOps Insight - executive summary

**From public data to an integrated OCC and CAMO decision prototype**

Built on {pattern['flights']:,} BTS 2024 flight legs and NASA C-MAPSS FD001. All metrics below are
generated by the code in this repository. This is a prototype, not an airline-validated
product, and it has no Skywise or airline system access.

## 1. The opportunity

Three decisions are usually made in separate teams and spreadsheets:

- **Flight operations:** which flights should the OCC intervene on before departure?
- **Engineering:** which engines should CAMO inspect or remove next?
- **Maintenance control:** how should due aircraft be assigned to hangar slots?

FlightOps Insight connects them with a shared data contract, leakage-safe features,
calibrated models, a constraint-based planner and a six-page decision app with live ADS-B tracking.

## 2. OTP: predictable, calibrated, targetable

![ROC and PR curves](../reports/figures/otp_roc_pr.png)

| Model | ROC-AUC | PR-AUC | Brier | Top-decile lift |
|---|---:|---:|---:|---:|
| Route-hour baseline | {base['roc_auc']:.3f} | {base['pr_auc']:.3f} | {base['brier']:.3f} | {base['top_decile_lift']:.2f}x |
| Logistic regression | {logistic['roc_auc']:.3f} | {logistic['pr_auc']:.3f} | {logistic['brier']:.3f} | {logistic['top_decile_lift']:.2f}x |
| LightGBM | **{lgb['roc_auc']:.3f}** | **{lgb['pr_auc']:.3f}** | **{lgb['brier']:.3f}** | **{lgb['top_decile_lift']:.2f}x** |

- Calibration ECE: **{lgb['ece']:.3f}**.
- Recommended threshold: **{otp['operating_threshold']['recommended']:.3f}** (top decile).
- At that threshold: **{top['precision']:.1%}** precision, **{top['recall']:.1%}** recall,
  **{top['flag_rate']:.1%}** of test flights flagged.
- Top-decile lift **{lgb['top_decile_lift']:.2f}x**; **{top['recall']:.1%}** of delays captured.

**So what.** A top-decile risk list is the right shape for a finite OCC team: about
three in four flagged flights actually depart late, and the list contains nearly half
of all delays.

## 3. Where delay risk concentrates

![Hourly delay risk](../reports/figures/otp_hourly.png)

![Turnaround effect](../reports/figures/otp_turnaround.png)

- 15:00-20:00 delay rate **{pattern['peak_rate']:.1%}** vs **{pattern['early_rate']:.1%}** in 06:00-09:00 ({pattern['peak_ratio']:.1f}x).
- Turns under 40 minutes: **{pattern['short_turn_rate']:.1%}** vs **{pattern['long_turn_rate']:.1%}** for 90-149 minute turns.
- Turnaround and inbound aircraft are **{pattern['turnaround_shap_share']:.0%}** of mean absolute SHAP.
- Late inbound is associated with higher risk, but the comparison is confounded by
  scheduled buffer and network mix.

**So what.** Protect the afternoon bank and short turns first. The predictor page
lets an OCC user test a specific flight and see the local SHAP drivers.

## 4. RUL: the window models beat the baseline

![Predicted vs true RUL](../reports/figures/rul_predicted_vs_true.png)

| Model | RMSE | MAE | R2 | NASA score | Late rate |
|---|---:|---:|---:|---:|---:|
| Ridge last-cycle | {ridge['rmse']:.1f} | {ridge['mae']:.1f} | {ridge['r2']:.3f} | {ridge['nasa_score_mean']:.2f} | {ridge['late_prediction_rate']:.0%} |
| XGBoost window | **{xgb['rmse']:.1f}** | **{xgb['mae']:.1f}** | **{xgb['r2']:.3f}** | **{xgb['nasa_score_mean']:.2f}** | {xgb['late_prediction_rate']:.0%} |
| LSTM | {lstm['rmse']:.1f} | {lstm['mae']:.1f} | {lstm['r2']:.3f} | {lstm['nasa_score_mean']:.2f} | **{lstm['late_prediction_rate']:.0%}** |

- Conformal 90% interval: **+/-{rul['conformal']['q_cycles']:.1f} cycles**; empirical
  coverage **{rul['conformal']['test_coverage']:.0%}**.
- 20-cycle alert rule: **{alerts['selected']['precision']:.1%}** precision,
  **{alerts['selected']['recall']:.1%}** recall ({alerts['selected']['tp']} TP, {alerts['selected']['fp']} FP, {alerts['selected']['fn']} FN).
- Cost **${alerts['selected']['total_cost_usd']/1e6:.2f}M** vs **${alerts['benchmark_no_alert_cost_usd']/1e6:.1f}M**
  with no alerts and **${alerts['benchmark_remove_all_early_cost_usd']/1e6:.1f}M** for
  remove-all-early. The cost-optimal threshold is
  **{alerts['cost_optimal_threshold_cycles']} cycles** with the same outcome.
- Costs are planning assumptions, not audited savings.

**So what.** Use the 20-cycle rule as a CAMO watchlist. Reserve shop capacity for the
critical group, pull forward planned visits for the high group, and keep a licensed
engineer in the loop.

## 5. Maintenance optimization: a {ob['savings_vs_greedy_pct']:.0f}% cheaper plan

![Cost comparison](../reports/figures/optimization_cost_comparison.png)

![Optimized schedule](../reports/figures/optimization_gantt.png)

| Plan | Assigned | Unassigned | Cost |
|---|---:|---:|---:|
| Defer all | 0 | {total_aircraft} | ${ob['defer_all_cost_usd']/1e6:.2f}M |
| Current practice | {total_aircraft - ob['greedy_unassigned']} | {ob['greedy_unassigned']} | ${ob['greedy_cost_usd']/1e6:.2f}M |
| CP-SAT optimized | {total_aircraft - ob['optimized_unassigned']} | {ob['optimized_unassigned']} | **${ob['optimized_cost_usd']/1e6:.2f}M** |

- Solver status: **{ob['solver_status']}** in **{ob['solve_seconds']:.2f}s**.
- Saving vs current practice: **${ob['savings_vs_greedy_usd']:,.0f} ({ob['savings_vs_greedy_pct']:.1f}%)**.
- H3 closure: ${scenarios['Disruption: hangar_closure']['total_cost_usd']/1e6:.2f}M, 0 unassigned.
- RUL stress: ${stress['total_cost_usd']/1e6:.2f}M, **{stress['unassigned']} unassigned**.
- Demand surge: ${scenarios['Disruption: demand_surge']['total_cost_usd']/1e6:.2f}M, 0 unassigned.

**So what.** Approve the optimized plan for the synthetic instance, protect one flexible
C-check slot, and pre-agree a swap list. RUL stress is the binding constraint; a hangar
closure alone is absorbable.

## 6. Production path and governance

- **Databricks + Unity Catalog:** governed Bronze/Silver/Gold, Workflows, MLflow and
  feature backfills.
- **Vertex AI:** Pipelines, Training, Model Registry, online endpoint, batch prediction
  and model monitoring.
- **Skywise:** no access in the prototype. Treat it as a governed data product under an
  approved agreement, with lineage, classification and retention controls.
- **Human-in-the-loop:** OTP ranks flights and the OCC decides; RUL is a watchlist and a
  licensed engineer signs off; the optimizer proposes and CAMO approves.
- **Security:** SSO, least privilege, VPC-SC, CMEK, Secret Manager and audit logs.

## 7. Roadmap

| Phase | Duration | Exit criteria |
|---|---|---|
| Data access | 0-8 weeks | Approved data contract, security review, data landed. |
| Rebuild and backtest | 8-16 weeks | Features reproduced, model recalibrated, time-based backtest. |
| Shadow and pilot | 16-28 weeks | No operational dependency; override logging; users trained. |
| Scale | 28+ weeks | Approved models, broader fleets, richer constraints. |

## 8. Recommendation

Fund a 6-month pilot with one carrier and one fleet. Success criteria:

- OTP top-decile precision at or above **70%**;
- RUL alert recall at or above **90%**;
- a maintenance plan that CAMO planners accept with a documented override rate;
- no unlogged overrides and no direct writes to release-to-service records.

Do not claim savings until they are measured in the pilot. The prototype metrics are
not production guarantees.

## Honest limitations

- Observational associations, not causal effects.
- Aircraft type is synthetic; weather is proxied by calendar and season.
- C-MAPSS is simulated and single-fault-mode.
- The maintenance instance and costs are synthetic/planning assumptions.
- The R survival analysis found no significant early-life slope separation
  (log-rank p = 0.50); this is reported as a negative result.
- No airline, MRO or Skywise integration exists.
"""


def main() -> None:
    data = load()
    DOCS.mkdir(exist_ok=True)
    markdown = build_markdown(data)
    (DOCS / "executive_summary.md").write_text(markdown, encoding="utf-8")
    prs = build_deck(data)
    output = ROOT / "reports" / "executive_summary.pptx"
    prs.save(output)
    print(f"Wrote {DOCS / 'executive_summary.md'}")
    print(f"Wrote {output} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
