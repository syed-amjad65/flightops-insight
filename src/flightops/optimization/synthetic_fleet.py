"""Synthetic fleet, maintenance demand and hangar-slot generation.

Everything here is synthetic and reproducible from ``project.seed``.  The
assumptions are deliberately explicit:

* Each aircraft has an A-check interval, a C-check interval and an engine RUL
  that together determine the next maintenance due date.
* A-check occupies one overnight ground window; C-check occupies a 36-hour
  hangar block (two calendar days of hangar capacity).
* Hangars have one aircraft position per day and fleet eligibility.
* Disruption cost is charged per flying day lost; early maintenance wastes
  remaining component life; flying past an engine due date carries an
  escalating AOG-risk penalty.

The data model is intentionally close to a CAMO maintenance-planning export so
a real feed can replace the generator without changing the solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from flightops.config import Config


@dataclass(frozen=True)
class Aircraft:
    tail: str
    fleet: str
    base: str
    due_day: int
    binding_driver: str
    check_type: str  # "A" or "C"
    hours_since_a: float
    hours_since_c: float
    engine_rul_cycles: float
    engine_rul_min_cycles: float
    daily_flight_hours: float
    daily_cycles: float
    flights_per_day: float
    min_ground_time_h: float
    a_due_day: int
    c_due_day: int
    engine_due_day: int

    @property
    def needs_maintenance(self) -> bool:
        return True


@dataclass(frozen=True)
class Slot:
    slot_id: str
    hangar: str
    base: str
    start_day: int
    duration_days: int
    check_type: str
    ground_time_h: float
    eligible_fleets: tuple[str, ...]
    days_covered: tuple[int, ...]

    def covers(self, day: int) -> bool:
        return day in self.days_covered


@dataclass
class MaintenanceInstance:
    aircraft: list[Aircraft]
    slots: list[Slot]
    horizon_days: int
    start_date: date
    cost_params: dict[str, float]
    scenario: str = "baseline"

    def eligible(self, aircraft: Aircraft, slot: Slot) -> bool:
        return (
            aircraft.fleet in slot.eligible_fleets
            and slot.check_type == aircraft.check_type
            and slot.ground_time_h >= aircraft.min_ground_time_h
        )

    def slot_cost(self, aircraft: Aircraft, slot: Slot) -> dict[str, float]:
        """Cost breakdown if this aircraft is assigned to this slot."""
        disruption_days = 0.5 if slot.check_type == "A" else 2.0
        ferry = 0.0 if aircraft.base == slot.base else float(self.cost_params["ferry_usd"])
        disruption = (
            disruption_days * float(self.cost_params["disruption_cost_usd_per_day"]) + ferry
        )
        early_days = max(0, aircraft.due_day - slot.start_day)
        late_days = max(0, slot.start_day - aircraft.due_day)
        early = early_days * float(self.cost_params["early_maintenance_cost_usd_per_day"])
        aog = (
            float(self.cost_params["aog_risk_penalty_usd_per_day"])
            * late_days
            * (late_days + 1)
            / 2.0
        )
        return {
            "disruption_usd": round(disruption, 2),
            "early_maintenance_usd": round(early, 2),
            "aog_risk_usd": round(aog, 2),
            "total_usd": round(disruption + early + aog, 2),
            "late_days": late_days,
            "early_days": early_days,
        }

    def unassigned_cost(self, aircraft: Aircraft) -> dict[str, float]:
        """Cost of leaving the aircraft unassigned (deferred/cancelled)."""
        days_late = max(0, self.horizon_days - max(aircraft.due_day, 0))
        disruption = 2.0 * float(self.cost_params["disruption_cost_usd_per_day"])
        aog = (
            float(self.cost_params["aog_risk_penalty_usd_per_day"])
            * days_late
            * (days_late + 1)
            / 2.0
        )
        total = float(self.cost_params["unassigned_penalty_usd"]) + disruption + aog
        return {
            "disruption_usd": round(disruption, 2),
            "aog_risk_usd": round(aog, 2),
            "unassigned_penalty_usd": float(self.cost_params["unassigned_penalty_usd"]),
            "total_usd": round(total, 2),
            "late_days": days_late,
        }


def _next_maintenance(aircraft: dict[str, Any], fleet_cfg: dict[str, Any]) -> tuple[int, str, str, int, int, int]:
    """Compute due days and the binding check driver for one aircraft."""
    a_remaining = max(0.0, float(fleet_cfg["a_check_interval_fh"]) - aircraft["hours_since_a"])
    c_remaining = max(0.0, float(fleet_cfg["c_check_interval_fh"]) - aircraft["hours_since_c"])
    engine_margin = 5.0
    engine_remaining = max(0.0, aircraft["engine_rul_min_cycles"] - engine_margin)
    a_due = int(math.floor(a_remaining / aircraft["daily_flight_hours"]))
    c_due = int(math.floor(c_remaining / aircraft["daily_flight_hours"]))
    engine_due = int(math.floor(engine_remaining / aircraft["daily_cycles"]))
    candidates = {"A-check interval": a_due, "C-check interval": c_due, "Engine RUL": engine_due}
    binding = min(candidates, key=candidates.get)
    check_type = "C" if binding in {"C-check interval", "Engine RUL"} else "A"
    return int(candidates[binding]), binding, check_type, a_due, c_due, engine_due


def build_instance(cfg: Config, scenario: str = "baseline") -> MaintenanceInstance:
    """Generate a reproducible maintenance-planning instance."""
    seed = int(cfg.get("project.seed", 42))
    rng = np.random.default_rng(seed)
    horizon = int(cfg.get("optimization.horizon_days", 14))
    start = date.fromisoformat(str(cfg.get("data.synthetic.start_date", "2026-11-09")))
    fleet_cfg = cfg.get("optimization.fleets", {})
    hangars = [dict(h) for h in cfg.get("optimization.hangars", [])]
    scenario_cfg = cfg.get("optimization.scenarios", {})

    if scenario == "hangar_closure":
        closed = str(scenario_cfg.get("hangar_closure", "H3"))
        hangars = [h for h in hangars if h["name"] != closed]
    rul_stress = float(scenario_cfg.get("rul_stress_pct", 0.25)) if scenario == "rul_stress" else 0.0
    demand_surge = float(scenario_cfg.get("demand_surge_pct", 0.10)) if scenario == "demand_surge" else 0.0

    fleet_counts = {"A320": 12, "B737": 10, "E190": 8}
    fleet_bases = {"A320": "ATL", "B737": "DFW", "E190": "ORD"}
    aircraft: list[Aircraft] = []
    index = 0
    for fleet, count in fleet_counts.items():
        fleet_data = fleet_cfg[fleet]
        for _ in range(count):
            index += 1
            hours_since_a = float(rng.uniform(0, fleet_data["a_check_interval_fh"] * 0.98))
            hours_since_c = float(rng.uniform(0, fleet_data["c_check_interval_fh"] * 0.98))
            engine_rul = float(rng.uniform(8, 190))
            if rul_stress:
                engine_rul *= 1.0 - rul_stress
            daily_fh = float(fleet_data["daily_flight_hours"]) * (1.0 + demand_surge)
            daily_cycles = float(fleet_data["daily_cycles"]) * (1.0 + demand_surge)
            record = {
                "hours_since_a": hours_since_a,
                "hours_since_c": hours_since_c,
                "engine_rul_min_cycles": engine_rul,
                "daily_flight_hours": daily_fh,
                "daily_cycles": daily_cycles,
            }
            due_day, binding, check_type, a_due, c_due, e_due = _next_maintenance(record, fleet_data)
            aircraft.append(
                Aircraft(
                    tail=f"N{700 + index:03d}FS",
                    fleet=fleet,
                    base=fleet_bases[fleet],
                    due_day=due_day,
                    binding_driver=binding,
                    check_type=check_type,
                    hours_since_a=round(hours_since_a, 1),
                    hours_since_c=round(hours_since_c, 1),
                    engine_rul_cycles=round(engine_rul, 1),
                    engine_rul_min_cycles=round(engine_rul, 1),
                    daily_flight_hours=round(daily_fh, 2),
                    daily_cycles=round(daily_cycles, 2),
                    flights_per_day=round(daily_cycles, 1),
                    min_ground_time_h=float(fleet_data["min_ground_time_h"]),
                    a_due_day=a_due,
                    c_due_day=c_due,
                    engine_due_day=e_due,
                )
            )

    slots: list[Slot] = []
    for hangar in hangars:
        for day in range(horizon):
            slots.append(
                Slot(
                    slot_id=f"{hangar['name']}-A-D{day:02d}",
                    hangar=str(hangar["name"]),
                    base=str(hangar["base"]),
                    start_day=day,
                    duration_days=1,
                    check_type="A",
                    ground_time_h=8.0,
                    eligible_fleets=tuple(hangar["fleets"]),
                    days_covered=(day,),
                )
            )
        if hangar.get("c_check_capable", False):
            for start_day in range(0, horizon - 1, 3):
                if start_day + 1 >= horizon:
                    continue
                slots.append(
                    Slot(
                        slot_id=f"{hangar['name']}-C-D{start_day:02d}",
                        hangar=str(hangar["name"]),
                        base=str(hangar["base"]),
                        start_day=start_day,
                        duration_days=2,
                        check_type="C",
                        ground_time_h=36.0,
                        eligible_fleets=tuple(hangar["fleets"]),
                        days_covered=(start_day, start_day + 1),
                    )
                )

    cost_params = {
        "disruption_cost_usd_per_day": float(cfg.get("optimization.disruption_cost_usd_per_day", 18000)),
        "early_maintenance_cost_usd_per_day": float(cfg.get("optimization.early_maintenance_cost_usd_per_day", 2500)),
        "aog_risk_penalty_usd_per_day": float(cfg.get("optimization.aog_risk_penalty_usd_per_day", 30000)),
        "unassigned_penalty_usd": float(cfg.get("optimization.unassigned_penalty_usd", 400000)),
        "ferry_usd": 5000.0,
    }
    # Only aircraft whose due date falls inside the planning horizon require a slot.
    aircraft = [a for a in aircraft if a.due_day <= horizon - 1]
    return MaintenanceInstance(
        aircraft=aircraft,
        slots=slots,
        horizon_days=horizon,
        start_date=start,
        cost_params=cost_params,
        scenario=scenario,
    )


def instance_frames(instance: MaintenanceInstance) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return tidy aircraft and slot tables for the app and exports."""
    aircraft_rows = []
    for aircraft in instance.aircraft:
        aircraft_rows.append(
            {
                "tail": aircraft.tail,
                "fleet": aircraft.fleet,
                "base": aircraft.base,
                "due_day": aircraft.due_day,
                "due_date": instance.start_date + timedelta(days=max(0, aircraft.due_day)),
                "binding_driver": aircraft.binding_driver,
                "check_type": aircraft.check_type,
                "hours_since_a": aircraft.hours_since_a,
                "hours_since_c": aircraft.hours_since_c,
                "engine_rul_cycles": aircraft.engine_rul_cycles,
                "daily_flight_hours": aircraft.daily_flight_hours,
                "daily_cycles": aircraft.daily_cycles,
                "a_due_day": aircraft.a_due_day,
                "c_due_day": aircraft.c_due_day,
                "engine_due_day": aircraft.engine_due_day,
            }
        )
    slot_rows = []
    for slot in instance.slots:
        slot_rows.append(
            {
                "slot_id": slot.slot_id,
                "hangar": slot.hangar,
                "base": slot.base,
                "start_day": slot.start_day,
                "start_date": instance.start_date + timedelta(days=slot.start_day),
                "duration_days": slot.duration_days,
                "check_type": slot.check_type,
                "ground_time_h": slot.ground_time_h,
                "eligible_fleets": ",".join(slot.eligible_fleets),
                "days_covered": ",".join(str(d) for d in slot.days_covered),
            }
        )
    return pd.DataFrame(aircraft_rows), pd.DataFrame(slot_rows)
