# Model card: maintenance plan optimizer

## 1. Model details

| Field | Value |
|---|---|
| Name | CP-SAT aircraft-to-hangar maintenance planner |
| Version | 0.1.0 prototype |
| Type | Constraint programming / binary integer optimization |
| Objective | Minimize disruption + early-maintenance waste + AOG risk + unassigned penalty |
| Owner (prototype) | FlightOps Insight |
| Intended users | CAMO planners, maintenance control, network operations |
| Decision supported | Propose a hangar assignment plan; human planners approve or override |

## 2. Intended use

Given a list of aircraft due for maintenance and a set of hangar slots, the optimizer
assigns each aircraft to an eligible slot or explicitly leaves it unassigned. It is
used to:

- compare current earliest-slot practice against a cost-minimizing plan;
- test hangar closure, RUL stress and demand surge;
- quantify the cost of a more or less risk-averse AOG policy;
- give planners a transparent, auditable schedule.

## 3. Out-of-scope use

Do not use this model to:

- approve an airworthiness deferral or release to service;
- schedule individual maintenance tasks, labour, parts or tools;
- replace the MRO system or licensed engineer;
- optimize crew, gates or passenger connections;
- operate on a real fleet without replacing the synthetic instance and validating the
  cost model.

## 4. Inputs

The prototype generates a **synthetic** instance from `project.seed`:

- 15 aircraft across A320, B737 and E190 fleets;
- 52 hangar slots across 3 hangars over 14 days;
- A-check and C-check demand;
- engine RUL values that can bind earlier than the check interval;
- hangar eligibility by fleet and check type;
- cost parameters from `config.yaml`.

The instance shape follows a CAMO export, but the values are not an airline schedule.

## 5. Decision variables and constraints

- Binary assignment \(x_{a,s}\) for each eligible aircraft-slot pair.
- Binary deferral \(u_a\) for each aircraft.
- Each aircraft is assigned once or explicitly deferred.
- At most one aircraft per hangar per day.
- Fleet, check-type, ground-time and horizon eligibility.
- Two-day C-checks consume two hangar-days.

The full formulation is in [optimization_model.md](../optimization_model.md).

## 6. Objective and cost assumptions

The objective is a weighted sum of:

| Component | Assumption |
|---|---|
| Flying disruption | $18,000 per affected flying day; A-check 0.5 day, C-check 2 days |
| Ferry | $12,000 if the hangar base differs from the aircraft base |
| Early-maintenance waste | $2,500 per day before the due date |
| AOG-risk penalty | $30,000 per aircraft-day, triangular in lateness |
| Unassigned penalty | $400,000 fixed plus disruption and AOG exposure |

These are **planning assumptions**, not audited airline actuals. The sensitivity
analysis shows how the plan changes when the AOG penalty is varied from $15k to $60k.

## 7. Results (baseline synthetic instance)

| Plan | Assigned | Unassigned | Total cost |
|---|---:|---:|---:|
| Defer all | 0 | 15 | $19,620,000 |
| Current practice (earliest feasible) | 14 | 1 | $1,197,500 |
| CP-SAT optimized | 15 | 0 | **$645,000** |

- Solver status: **OPTIMAL**.
- Solve time: **0.03 s**.
- Savings versus current practice: **$552,500 (46.1%)**.
- Savings versus defer-all: **$18,975,000**.

Disruption scenarios:

| Scenario | Optimized cost | Unassigned | Greedy cost | Savings vs greedy |
|---|---:|---:|---:|---:|
| Hangar H3 closure | $657,500 | 0 | $1,187,500 | $530,000 |
| RUL stress (-25%) | $3,641,500 | 3 | $3,744,000 | $102,500 |
| Demand surge (+10%) | $785,500 | 0 | $940,500 | $155,000 |

The RUL-stress case is the binding constraint: three aircraft cannot be placed inside
the horizon even with all hangars open. This is the scenario that should drive
contingency planning.

## 8. Validation

Every solution is checked by `validate_plan`:

- every aircraft is assigned or explicitly unassigned;
- every assignment is eligible;
- no slot is used more than once;
- no assignment is outside the horizon.

If CP-SAT does not return `OPTIMAL` or `FEASIBLE`, the planner falls back to the greedy
plan and logs a warning. Unit tests cover capacity, eligibility, double-booking and
the deferral path.

## 9. Limitations and risks

- Synthetic fleet, slots and costs.
- Deterministic durations; no stochastic repair times.
- No parts, labour, tools, crew or shift constraints.
- No task-level precedence within a check.
- No rolling-horizon freezing or disruption re-optimization.
- The AOG penalty is a policy choice; it is not a safety case.
- The model may exploit cost assumptions in ways a human planner would reject.
- The objective does not include passenger compensation, brand or regulatory penalties.

## 10. Human-in-the-loop

The optimizer proposes; CAMO disposes. Required controls:

- planner review of every assignment and deferral;
- engineer sign-off for any aircraft scheduled past its due date;
- reason codes for overrides;
- audit trail of plan versions, inputs and solver status;
- escalation when the unassigned count is greater than zero;
- no automatic release to the MRO system without approval.

## 11. Monitoring

Track:

- solver status and solve time;
- unassigned count and reason;
- lateness days and AOG exposure;
- early-maintenance waste;
- override rate and reason;
- actual versus planned check duration;
- parts and labour availability;
- schedule adherence after the plan is frozen.

## 12. Artifacts

- `reports/optimization/instance_aircraft.csv`
- `reports/optimization/instance_slots.csv`
- `reports/optimization/maintenance_plan_optimized.csv`
- `reports/optimization/maintenance_plan_greedy.csv`
- `reports/optimization/scenario_results.json`
- `reports/optimization/sensitivity_aog_penalty.csv`
- `reports/figures/optimization_gantt.png`
- `reports/figures/optimization_cost_comparison.png`
