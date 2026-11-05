# Limitations and assumptions

This document is deliberately blunt. The prototype is useful only if its limits are
as visible as its results.

## 1. Scope

FlightOps Insight is a **decision-support prototype** built on public data and a
synthetic maintenance instance. It is not an airline system, not a certified tool and
not a production service.

It does not have access to:

- Airbus Skywise or any airline data platform;
- airline schedule, fleet, crew, gate or passenger data;
- MRO work orders, parts, labour or borescope records;
- weather, ATC flow or airport capacity feeds;
- airline cost accounting or P&L data.

No result should be read as an airline-validated outcome.

## 2. Data limitations

### BTS OTP

- U.S. domestic operations only; no international network.
- Timestamps are local airport times and are kept naive. No cross-airport UTC
  conversion is applied.
- The raw feed has no aircraft equipment field. Aircraft type is a deterministic
  synthetic enrichment.
- Delay causes (carrier, weather, NAS, security, late aircraft) are post-flight
  attributions and are excluded from model features.
- Weather is represented only through season, calendar and time-of-day proxies.
- Cancelled and diverted flights are not fully modelled; the OTP target is departure
  delay among operated flights.
- Reporting practices and schedule changes can create structural breaks.

### C-MAPSS

- Simulated turbofan degradation, not a fleet feed.
- FD001 has one operating condition and one fault mode.
- Training units run to failure; real fleets have repairs, censoring and mixed
  operating regimes.
- The dataset does not include maintenance actions, borescope, oil analysis or OEM
  limits.
- The official test set has 100 engines, so metric uncertainty is wide.
- RUL is capped at 125 cycles, which compresses early-life labels.

### Synthetic maintenance instance

- 15 aircraft, 52 slots, 3 hangars; not an airline schedule.
- Due dates, RUL values, durations and eligibility are generated from a seed.
- Cost parameters are planning assumptions.
- The instance shape is realistic, but the values are not.

### Live ADS-B traffic

- The OpenSky Network is crowd-sourced; coverage is uneven, especially over
  oceans, remote regions and at low altitude.
- Positions can lag by seconds to minutes. The page shows the age of the last
  contact for each aircraft.
- Barometric altitude is not corrected to a local altimeter setting.
- Callsigns may be missing or duplicated. ICAO24 is the stable identifier.
- The public API is rate limited and has no service-level agreement.
- When OpenSky is unreachable the page may show a seeded demo snapshot. It is
  labelled as demo and must not be mistaken for live traffic.
- This feed is for situational awareness only. It is **not** surveillance-grade
  and must not be used for separation, dispatch, collision avoidance or any
  safety-critical decision.

## 3. Modelling limitations

### OTP

- Observational associations, not causal effects.
- No weather, crew, gate, aircraft rotation or passenger connection data.
- Aircraft type is synthetic.
- Carrier and route effects may encode network structure and schedule design rather
  than operational quality.
- The model is trained on 2024; it will drift with schedule, fleet, airport and policy
  changes.
- The recommended threshold is a capacity choice, not a universal optimum.
- The cost-optimal validation threshold (0.05) flags far too many flights for a
  finite OCC team; it is reported for transparency, not recommended.
- No protected-class fairness audit is possible with BTS data.

### RUL

- Simulated degradation and a single fault mode.
- XGBoost has the best RMSE; the LSTM has the lowest late-prediction rate. Neither is
  uniformly best.
- Conformal coverage is 83% against a 90% target on 100 test engines.
- The alert cost model is illustrative.
- No per-prediction sensor attribution is currently exposed.
- The model cannot distinguish "low RUL" from "unsafe now"; engineering judgement is
  mandatory.

### Optimization

- Synthetic instance and deterministic durations.
- No parts, labour, tools, crew or shift constraints.
- No task-level precedence.
- No rolling-horizon freezing or disruption re-optimization.
- The AOG penalty is a policy choice, not a safety case.
- The model may exploit cost assumptions in ways a human planner would reject.
- It is an assignment model, not a full maintenance programme optimizer.

## 4. Evaluation limitations

- OTP test months (Nov-Dec 2024) are a single season; performance in summer may differ.
- RUL test engines are only 100; confidence intervals around RMSE and coverage are wide.
- The optimizer is evaluated on one synthetic instance and three perturbations.
- The R statistical tests are associational and use very large samples, so p-values are
  tiny even for small effects. Effect sizes and operational relevance matter more.
- The R survival analysis found **no significant separation** by early-life sensor
  slope (log-rank p = 0.50), and Cox covariates were not individually significant.
  This is reported as a negative result, not hidden.
- No prospective or shadow-mode evaluation has been performed.

## 5. Business-case limitations

- Intervention cost, delay cost, passenger re-accommodation, prevention rate,
  unscheduled-removal cost and early-removal cost are configurable planning
  assumptions.
- The OTP business case assumes a share of flagged delays is preventable. This is not
  a measured treatment effect.
- The RUL cost comparison assumes a fixed safety event (`true RUL <= 20`) and does not
  include secondary damage, lease penalties or regulatory costs.
- The optimizer's disruption and AOG penalties are not audited P&L figures.
- Savings are scenario arithmetic, not verified savings.

## 6. Operational and human factors

- OTP predictions rank flights; they do not make decisions.
- RUL predictions are a watchlist; a licensed engineer must approve any removal,
  deferral or release-to-service decision.
- The optimizer proposes a plan; CAMO planners must review and approve it.
- Overrides, near-misses and reasons must be logged.
- The app does not integrate with OCC, MRO or flight-planning systems.
- The app has no authentication, authorization or audit trail in the prototype.
- The prototype has no service-level objective, on-call rotation or incident process.

## 7. Security, privacy and governance gaps

- No PII is used, but a production system would need data classification and access
  controls.
- No secrets management; no credentials are stored in the repo.
- No SSO, role-based access or tenant isolation.
- No model-risk approval, fairness review or regulatory sign-off.
- No immutable model lineage or decision audit trail beyond local logs.
- No data-retention or deletion policy.
- No vendor or open-source licence review for production distribution.

## 8. What cannot be concluded

The prototype **cannot** conclude that:

- a specific flight will be late;
- a specific engine will fail at a specific cycle;
- a short turnaround causes a delay;
- the model will save a specific amount of money at an airline;
- the optimizer's plan is airworthy or operationally feasible;
- the results transfer to another carrier, region, fleet or year;
- Skywise or any airline system has been integrated.

## 9. Minimum conditions for a pilot

Before any airline pilot, the following would be required:

1. Replace public data with approved airline data under a data contract.
2. Rebuild the aircraft-type enrichment from the fleet registry.
3. Join weather, ATC, crew, gate and rotation data where lawful.
4. Recalibrate and backtest on the airline's own history.
5. Run in shadow mode for at least one full seasonal cycle.
6. Complete security, privacy, model-risk and safety reviews.
7. Define human-in-the-loop approvals, override reasons and audit trails.
8. Agree monitoring, retraining and rollback ownership.
9. Validate cost assumptions with finance and maintenance control.
10. Obtain licensed engineer and CAMO sign-off for any maintenance use.

## 10. Honest summary

The prototype demonstrates a credible **shape** for an integrated Flight Ops and CAMO
decision-support product: public-data ingestion, leakage-safe features, calibrated
models, explainability, a constraint-based planner and a decision app. It does not
demonstrate airline-scale accuracy, savings or safety. Those require airline data,
operational validation and governance.
