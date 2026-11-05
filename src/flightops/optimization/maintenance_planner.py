"""CP-SAT aircraft-to-maintenance-slot optimizer.

Mathematical model (also documented in docs/optimization_model.md)
-------------------------------------------------------------------
Sets
  A  aircraft requiring maintenance inside the horizon
  S  hangar slots; each slot has hangar h, start day d, duration, check type
     and eligible fleet set
  D  hangar-days (h, day) covered by slots

Decision variables
  x[a,s] in {0,1}  aircraft a assigned to slot s
  u[a]   in {0,1}  aircraft a left unassigned (deferred/cancelled)

Constraints
  (1) sum_s x[a,s] + u[a] = 1                    for every a
  (2) sum_{a,s: day in s} x[a,s] <= 1             for every hangar-day
      (hangar capacity of one aircraft position per day)
  (3) x[a,s] = 0 if fleet incompatible or check type / ground time mismatch
  (4) x[a,s] = 0 outside the planning horizon

Objective
  minimise sum_{a,s} c[a,s] x[a,s] + sum_a c_unassigned[a] u[a]
  where c includes lost flying days, ferry, early-maintenance waste and an
  escalating AOG-risk penalty for using a slot after the RUL/check due date.

The model is linear; the convex AOG penalty is pre-computed per pair.  In
Gurobi/CPLEX the same model is a mixed-integer program with binary x/u, the
same assignment and capacity rows, and a linear objective; see the production
mapping document for API-level equivalents.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pandas as pd
from ortools.sat.python import cp_model

from flightops.config import Config
from flightops.logging_utils import get_logger
from flightops.optimization.synthetic_fleet import (
    Aircraft,
    MaintenanceInstance,
    Slot,
    build_instance,
)

logger = get_logger("optimization.planner")


@dataclass
class MaintenancePlan:
    assignments: dict[str, str] = field(default_factory=dict)  # tail -> slot_id
    unassigned: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    status: str = "UNKNOWN"
    solve_seconds: float = 0.0
    scenario: str = "baseline"
    objective_value: float | None = None

    @property
    def assigned_count(self) -> int:
        return len(self.assignments)

    @property
    def unassigned_count(self) -> int:
        return len(self.unassigned)


def evaluate_plan(instance: MaintenanceInstance, assignments: dict[str, str]) -> MaintenancePlan:
    """Score any assignment dictionary with the shared cost function."""
    slot_by_id = {slot.slot_id: slot for slot in instance.slots}
    total = 0.0
    breakdown = {"disruption_usd": 0.0, "early_maintenance_usd": 0.0, "aog_risk_usd": 0.0, "unassigned_penalty_usd": 0.0}
    unassigned: list[str] = []
    for aircraft in instance.aircraft:
        slot_id = assignments.get(aircraft.tail)
        if slot_id is None:
            cost = instance.unassigned_cost(aircraft)
            unassigned.append(aircraft.tail)
        else:
            slot = slot_by_id[slot_id]
            cost = instance.slot_cost(aircraft, slot)
        total += cost["total_usd"]
        for key in ("disruption_usd", "early_maintenance_usd", "aog_risk_usd", "unassigned_penalty_usd"):
            breakdown[key] += cost.get(key, 0.0)
    return MaintenancePlan(
        assignments=dict(assignments),
        unassigned=unassigned,
        total_cost_usd=round(total, 2),
        breakdown={key: round(value, 2) for key, value in breakdown.items()},
    )


def validate_plan(instance: MaintenanceInstance, plan: MaintenancePlan) -> list[str]:
    """Return a list of constraint violations; empty means feasible."""
    errors: list[str] = []
    aircraft_by_tail = {a.tail: a for a in instance.aircraft}
    slot_by_id = {s.slot_id: s for s in instance.slots}
    assigned_slots: list[str] = []
    for tail in plan.assignments:
        if tail not in aircraft_by_tail:
            errors.append(f"Unknown aircraft {tail}")
    for aircraft in instance.aircraft:
        if aircraft.tail not in plan.assignments and aircraft.tail not in plan.unassigned:
            errors.append(f"Aircraft {aircraft.tail} is neither assigned nor unassigned")
    for tail, slot_id in plan.assignments.items():
        aircraft = aircraft_by_tail.get(tail)
        slot = slot_by_id.get(slot_id)
        if aircraft is None or slot is None:
            errors.append(f"Unknown assignment {tail}->{slot_id}")
            continue
        if not instance.eligible(aircraft, slot):
            errors.append(f"Ineligible assignment {tail}->{slot_id}")
        assigned_slots.append(slot_id)
    if len(assigned_slots) != len(set(assigned_slots)):
        errors.append("A slot is assigned to more than one aircraft")
    for slot in instance.slots:
        users = [tail for tail, sid in plan.assignments.items() if sid == slot.slot_id]
        if users and slot.start_day >= instance.horizon_days:
            errors.append(f"Slot {slot.slot_id} outside horizon")
    return errors


def _greedy_assign(instance: MaintenanceInstance, strategy: str = "earliest") -> dict[str, str]:
    """Simple current-practice baseline: first available feasible slot."""
    used: set[str] = set()
    assignments: dict[str, str] = {}
    slots_by_type = {
        "A": sorted([s for s in instance.slots if s.check_type == "A"], key=lambda s: (s.start_day, s.hangar)),
        "C": sorted([s for s in instance.slots if s.check_type == "C"], key=lambda s: (s.start_day, s.hangar)),
    }
    for aircraft in sorted(instance.aircraft, key=lambda a: (a.due_day, a.tail)):
        candidates = [
            slot
            for slot in slots_by_type[aircraft.check_type]
            if slot.slot_id not in used and instance.eligible(aircraft, slot)
        ]
        if not candidates:
            continue
        if strategy == "earliest":
            choice = min(candidates, key=lambda s: (s.start_day, abs(s.start_day - aircraft.due_day), s.hangar))
        else:
            choice = min(candidates, key=lambda s: (abs(s.start_day - aircraft.due_day), s.start_day))
        assignments[aircraft.tail] = choice.slot_id
        used.add(choice.slot_id)
    return assignments


def greedy_plan(instance: MaintenanceInstance, strategy: str = "earliest") -> MaintenancePlan:
    assignments = _greedy_assign(instance, strategy=strategy)
    plan = evaluate_plan(instance, assignments)
    plan.status = "GREEDY"
    plan.scenario = instance.scenario
    return plan


def defer_all_plan(instance: MaintenanceInstance) -> MaintenancePlan:
    plan = evaluate_plan(instance, {})
    plan.status = "DEFER_ALL"
    plan.scenario = instance.scenario
    return plan


def solve_maintenance_plan(instance: MaintenanceInstance, cfg: Config) -> MaintenancePlan:
    """Solve the CP-SAT assignment model and return a validated plan."""
    started = time.perf_counter()
    model = cp_model.CpModel()
    x: dict[tuple[str, str], cp_model.IntVar] = {}
    u: dict[str, cp_model.IntVar] = {}
    pair_costs: dict[tuple[str, str], float] = {}
    unassigned_costs: dict[str, float] = {}

    for aircraft in instance.aircraft:
        variables = []
        for slot in instance.slots:
            if not instance.eligible(aircraft, slot):
                continue
            variable = model.NewBoolVar(f"x_{aircraft.tail}_{slot.slot_id}")
            x[(aircraft.tail, slot.slot_id)] = variable
            pair_costs[(aircraft.tail, slot.slot_id)] = instance.slot_cost(aircraft, slot)["total_usd"]
            variables.append(variable)
        defer = model.NewBoolVar(f"u_{aircraft.tail}")
        u[aircraft.tail] = defer
        unassigned_costs[aircraft.tail] = instance.unassigned_cost(aircraft)["total_usd"]
        model.Add(sum(variables) + defer == 1)

    # Hangar-day capacity: at most one aircraft position per hangar per day.
    for hangar in sorted({slot.hangar for slot in instance.slots}):
        for day in range(instance.horizon_days):
            covering = []
            for slot in instance.slots:
                if slot.hangar == hangar and slot.covers(day):
                    covering.extend(
                        variable
                        for (tail, slot_id), variable in x.items()
                        if slot_id == slot.slot_id
                    )
            if covering:
                model.Add(sum(covering) <= 1)

    objective_terms = [
        int(round(pair_costs[(tail, slot_id)])) * variable
        for (tail, slot_id), variable in x.items()
    ]
    objective_terms += [
        int(round(unassigned_costs[tail])) * variable for tail, variable in u.items()
    ]
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(cfg.get("optimization.solver_time_limit_s", 20))
    solver.parameters.num_search_workers = int(cfg.get("optimization.solver_workers", 8))
    solver.parameters.random_seed = int(cfg.get("project.seed", 42))
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    assignments: dict[str, str] = {}
    unassigned: list[str] = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for aircraft in instance.aircraft:
            chosen = None
            for slot in instance.slots:
                variable = x.get((aircraft.tail, slot.slot_id))
                if variable is not None and solver.Value(variable) == 1:
                    chosen = slot.slot_id
                    break
            if chosen is None:
                unassigned.append(aircraft.tail)
            else:
                assignments[aircraft.tail] = chosen
    else:
        logger.warning("CP-SAT status=%s; falling back to greedy plan", status_name)
        return greedy_plan(instance, strategy="earliest")

    plan = evaluate_plan(instance, assignments)
    plan.unassigned = unassigned
    plan.status = status_name
    plan.solve_seconds = round(time.perf_counter() - started, 3)
    plan.scenario = instance.scenario
    plan.objective_value = round(float(solver.ObjectiveValue()), 2)
    violations = validate_plan(instance, plan)
    if violations:
        raise RuntimeError(f"Solver returned invalid plan: {violations[:5]}")
    logger.info(
        "Solved %s: status=%s cost=%.0f assigned=%d unassigned=%d time=%.2fs",
        instance.scenario,
        status_name,
        plan.total_cost_usd,
        plan.assigned_count,
        plan.unassigned_count,
        plan.solve_seconds,
    )
    return plan


def plan_frame(instance: MaintenanceInstance, plan: MaintenancePlan) -> pd.DataFrame:
    """Tidy assignment table for dashboard and CSV export."""
    slot_by_id = {slot.slot_id: slot for slot in instance.slots}
    rows = []
    for aircraft in instance.aircraft:
        slot_id = plan.assignments.get(aircraft.tail)
        if slot_id is None:
            rows.append(
                {
                    "tail": aircraft.tail,
                    "fleet": aircraft.fleet,
                    "base": aircraft.base,
                    "check_type": aircraft.check_type,
                    "binding_driver": aircraft.binding_driver,
                    "due_day": aircraft.due_day,
                    "due_date": instance.start_date + timedelta(days=max(0, aircraft.due_day)),
                    "assigned_slot": "UNASSIGNED",
                    "hangar": "",
                    "slot_start_day": None,
                    "late_days": max(0, instance.horizon_days - max(aircraft.due_day, 0)),
                    "early_days": 0,
                    "disruption_usd": None,
                    "early_maintenance_usd": None,
                    "aog_risk_usd": None,
                    "unassigned_penalty_usd": instance.unassigned_cost(aircraft)["unassigned_penalty_usd"],
                    "total_cost_usd": instance.unassigned_cost(aircraft)["total_usd"],
                    "status": "Deferred / cancelled",
                }
            )
            continue
        slot = slot_by_id[slot_id]
        cost = instance.slot_cost(aircraft, slot)
        rows.append(
            {
                "tail": aircraft.tail,
                "fleet": aircraft.fleet,
                "base": aircraft.base,
                "check_type": aircraft.check_type,
                "binding_driver": aircraft.binding_driver,
                "due_day": aircraft.due_day,
                "due_date": instance.start_date + timedelta(days=max(0, aircraft.due_day)),
                "assigned_slot": slot.slot_id,
                "hangar": slot.hangar,
                "slot_start_day": slot.start_day,
                "late_days": cost["late_days"],
                "early_days": cost["early_days"],
                "disruption_usd": cost["disruption_usd"],
                "early_maintenance_usd": cost["early_maintenance_usd"],
                "aog_risk_usd": cost["aog_risk_usd"],
                "unassigned_penalty_usd": 0.0,
                "total_cost_usd": cost["total_usd"],
                "status": "Scheduled",
            }
        )
    return pd.DataFrame(rows)
