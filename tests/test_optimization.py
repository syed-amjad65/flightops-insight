"""Constraint tests for the CP-SAT maintenance planner."""

from __future__ import annotations

import pytest

from flightops.config import load_config
from flightops.optimization.maintenance_planner import (
    defer_all_plan,
    greedy_plan,
    plan_frame,
    solve_maintenance_plan,
    validate_plan,
)
from flightops.optimization.synthetic_fleet import build_instance


@pytest.fixture(scope="module")
def instance():
    return build_instance(load_config(), scenario="baseline")


def test_every_aircraft_is_assigned_once(instance) -> None:
    plan = solve_maintenance_plan(instance, load_config())
    assert plan.assigned_count + plan.unassigned_count == len(instance.aircraft)
    assert len(plan.assignments) == len(set(plan.assignments))
    assert validate_plan(instance, plan) == []


def test_hangar_capacity_is_never_exceeded(instance) -> None:
    plan = solve_maintenance_plan(instance, load_config())
    slot_lookup = {slot.slot_id: slot for slot in instance.slots}
    for hangar in {slot.hangar for slot in instance.slots}:
        for day in range(instance.horizon_days):
            users = [
                tail
                for tail, slot_id in plan.assignments.items()
                if slot_lookup[slot_id].hangar == hangar and slot_lookup[slot_id].covers(day)
            ]
            assert len(users) <= 1


def test_assignments_are_fleet_and_check_type_compatible(instance) -> None:
    plan = solve_maintenance_plan(instance, load_config())
    aircraft = {a.tail: a for a in instance.aircraft}
    slots = {s.slot_id: s for s in instance.slots}
    for tail, slot_id in plan.assignments.items():
        assert instance.eligible(aircraft[tail], slots[slot_id])


def test_greedy_and_defer_are_finite_and_distinct(instance) -> None:
    greedy = greedy_plan(instance)
    defer = defer_all_plan(instance)
    assert greedy.total_cost_usd >= 0
    assert defer.total_cost_usd > greedy.total_cost_usd
    assert greedy.assigned_count >= 1
    assert defer.assigned_count == 0


def test_plan_frame_has_one_row_per_aircraft(instance) -> None:
    plan = solve_maintenance_plan(instance, load_config())
    frame = plan_frame(instance, plan)
    assert len(frame) == len(instance.aircraft)
    assert frame["tail"].is_unique


def test_hangar_closure_reduces_slots() -> None:
    cfg = load_config()
    baseline = build_instance(cfg, scenario="baseline")
    closed = build_instance(cfg, scenario="hangar_closure")
    assert len(closed.slots) < len(baseline.slots)
    assert "H3" not in {slot.hangar for slot in closed.slots}
