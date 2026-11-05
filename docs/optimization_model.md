# Maintenance optimization model

## 1. Purpose

The optimizer answers a narrow but high-value question:

> Given the aircraft that must be maintained inside a 14-day horizon, which hangar
> slot should each aircraft use so that flying disruption, early-maintenance waste,
> AOG-risk exposure and unassigned aircraft are minimized?

It is an assignment model, not a full maintenance-programme optimizer. It does not
schedule individual tasks, parts, technicians or crew. Those belong in the MRO system.

## 2. Sets and indices

| Symbol | Meaning |
|---|---|
| \(A\) | Aircraft requiring maintenance inside the horizon. |
| \(S\) | Hangar slots. Each slot has a hangar, start day, duration, check type, eligible fleets and ground time. |
| \(S_a \subseteq S\) | Slots eligible for aircraft \(a\): fleet match, check-type match, ground-time match. |
| \(H\) | Hangars. |
| \(D\) | Days in the horizon, \(d = 0,\ldots,13\). |
| \(S_{h,d}\) | Slots in hangar \(h\) that cover day \(d\). |

## 3. Parameters

| Symbol | Meaning | Source |
|---|---|---|
| \(c_{a,s}\) | Cost of assigning aircraft \(a\) to slot \(s\). | Pre-computed from the cost model below. |
| \(c^{\text{unassigned}}_a\) | Cost of leaving aircraft \(a\) unassigned. | Pre-computed. |
| \(\text{due}_a\) | Due day for aircraft \(a\), from the binding A-check, C-check or engine-RUL limit. | Synthetic fleet generator. |
| \(\text{start}_s\) | Slot start day. | Slot table. |
| \(\text{dur}_s\) | Slot duration in days (1 for A, 2 for C). | Slot table. |
| \(F_s\) | Eligible fleets for slot \(s\). | Hangar configuration. |
| \(g_s\) | Ground time available in slot \(s\). | Hangar configuration. |
| \(g_a\) | Minimum ground time for aircraft \(a\). | Fleet configuration. |

## 4. Decision variables

\[
x_{a,s} \in \{0,1\}
\quad\text{for } a \in A,\ s \in S_a
\]

1 if aircraft \(a\) is assigned to slot \(s\).

\[
u_a \in \{0,1\}
\quad\text{for } a \in A
\]

1 if aircraft \(a\) is left unassigned (deferred, cancelled or sent outside the
horizon).

## 5. Constraints

### 5.1 Each aircraft is assigned exactly once or explicitly deferred

\[
\sum_{s \in S_a} x_{a,s} + u_a = 1
\quad \forall a \in A
\]

This prevents silent dropping: every aircraft is either in a slot or counted as
unassigned and penalized.

### 5.2 Hangar-day capacity

\[
\sum_{a \in A} \sum_{s \in S_{h,d}} x_{a,s} \le 1
\quad \forall h \in H,\ d \in D
\]

Each hangar has one aircraft position per day. A two-day C-check consumes two
hangar-days, so two C-checks cannot overlap in the same hangar.

### 5.3 Eligibility

\[
x_{a,s} = 0
\quad\text{if } \text{fleet}_a \notin F_s
\text{ or } \text{check}_a \ne \text{check}_s
\text{ or } g_s < g_a
\]

This is enforced by only creating variables for eligible pairs.

### 5.4 Horizon

\[
x_{a,s} = 0
\quad\text{if } \text{start}_s + \text{dur}_s > |D|
\]

Slots outside the planning horizon are not created.

## 6. Objective

\[
\min \sum_{a \in A}\sum_{s \in S_a} c_{a,s} x_{a,s}
+ \sum_{a \in A} c^{\text{unassigned}}_a u_a
\]

### 6.1 Assignment cost \(c_{a,s}\)

The assignment cost is the sum of three components:

\[
c_{a,s} = c^{\text{disruption}}_{a,s}
+ c^{\text{early}}_{a,s}
+ c^{\text{AOG}}_{a,s}
\]

**Flying disruption.** A-check loses half a flying day; C-check loses two flying
days. A ferry cost applies when the aircraft base differs from the hangar base.

\[
c^{\text{disruption}}_{a,s}
= \text{days}_s \cdot C^{\text{disruption}}
+ \text{ferry}_{a,s}
\]

with \(\text{days}_s = 0.5\) for A-check and \(2.0\) for C-check.

**Early-maintenance waste.** Scheduling before the due date wastes remaining
component life:

\[
c^{\text{early}}_{a,s}
= \max(0, \text{due}_a - \text{start}_s) \cdot C^{\text{early}}
\]

**AOG-risk penalty.** Scheduling after the due date carries an escalating risk. The
prototype uses a triangular penalty:

\[
c^{\text{AOG}}_{a,s}
= C^{\text{AOG}} \cdot \frac{\ell(\ell+1)}{2},
\quad \ell = \max(0, \text{start}_s - \text{due}_a)
\]

The triangular form is intentional: one day late is costly, two days late is much
more costly. It is pre-computed per pair, so the model stays linear.

### 6.2 Unassigned cost

Leaving an aircraft unassigned is treated as a deferred/cancelled maintenance event:

\[
c^{\text{unassigned}}_a
= 2 \cdot C^{\text{disruption}}
+ C^{\text{AOG}} \cdot \frac{\ell_a(\ell_a+1)}{2}
+ C^{\text{unassigned}}
\]

where \(\ell_a = \max(0, |D| - \max(\text{due}_a, 0))\) is the number of days the
aircraft would run past its due date if deferred to the end of the horizon, and
\(C^{\text{unassigned}}\) is a fixed penalty. In the baseline instance this makes
deferral unattractive but not infinitely expensive, so the solver can expose a true
capacity shortage instead of pretending one exists.

## 7. Cost parameters

All values live in `config.yaml` and are planning assumptions, not audited actuals.

| Parameter | Value | Meaning |
|---|---|---|
| `disruption_cost_usd_per_day` | $18,000 | Lost margin and reaccommodation per affected flying day. |
| `early_maintenance_cost_usd_per_day` | $2,500 | Value of remaining component life consumed early. |
| `aog_risk_penalty_usd_per_day` | $30,000 | Base penalty for operating past a due date. |
| `unassigned_penalty_usd` | $400,000 | Fixed cost of leaving an aircraft unassigned. |
| `ferry_usd` | $12,000 (instance default) | Ferry between base and hangar. |

## 8. Baselines and scenarios

| Policy | Definition | Purpose |
|---|---|---|
| Defer all | Assign nothing; pay the unassigned cost. | Upper bound and a sanity check that the model does not defer by default. |
| Current practice | Greedy earliest feasible slot, aircraft sorted by due day. | Realistic operational baseline. |
| CP-SAT optimized | Solve the model to optimality within the time limit. | Decision proposal. |

Scenarios perturb the same instance:

| Scenario | Perturbation | Question |
|---|---|---|
| Baseline | None. | What is the best plan? |
| `hangar_closure` | Remove hangar H3. | Can the plan absorb losing a hangar? |
| `rul_stress` | Reduce engine RUL by 25%. | What happens when degradation accelerates? |
| `demand_surge` | Increase flying demand by 10%. | What happens when the fleet is used harder? |

Sensitivity sweeps the AOG-risk penalty from $15k to $60k per aircraft-day. This is a
policy lever: a more risk-averse CAMO will pay more early-maintenance waste to avoid
late slots.

## 9. Solution and validation

The baseline instance has 15 aircraft, 52 slots and 3 hangars. CP-SAT proves
optimality in about **0.03 seconds**.

| Plan | Assigned | Unassigned | Total cost |
|---|---:|---:|---:|
| Defer all | 0 | 15 | $19,620,000 |
| Current practice (earliest feasible) | 14 | 1 | $1,197,500 |
| CP-SAT optimized | 15 | 0 | **$645,000** |

Savings versus current practice: **$552,500 (46.1%)**. Savings versus defer-all:
**$18,975,000**.

Every solver result is passed through `validate_plan`, which checks:

- every aircraft is assigned or explicitly unassigned;
- every assignment is eligible;
- no slot is used twice;
- no slot lies outside the horizon.

If CP-SAT returns neither `OPTIMAL` nor `FEASIBLE`, the code falls back to the greedy
plan and logs a warning rather than returning an invalid schedule.

## 10. What the model does not do

- It does not schedule individual maintenance tasks or labour.
- It does not model parts lead times, technician skills or shift patterns.
- It does not model crew, gate or passenger connections.
- It does not decide airworthiness; a licensed engineer must approve every removal,
  deferral or release-to-service decision.
- It assumes deterministic durations and costs. A production version should use
  stochastic durations or add buffer constraints.

## 11. Production extensions

1. Add task-level precedence and labour constraints (RCPSP-style).
2. Add parts availability and AOG spares as inventory constraints.
3. Add crew and passenger connection costs to the disruption term.
4. Use rolling-horizon re-optimization with frozen near-term assignments.
5. Add scenario-based robust optimization over RUL uncertainty.
6. Integrate with the MRO system for work-order release and sign-off.
