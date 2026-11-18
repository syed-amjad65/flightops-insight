# FlightOps Insight

[![CI](https://github.com/syed-amjad65/flightops-insight/actions/workflows/ci.yml/badge.svg)](https://github.com/syed-amjad65/flightops-insight/actions/workflows/ci.yml)

A local, end-to-end decision-support prototype for airline Flight Operations and
Continuing Airworthiness Management (CAMO). It combines:

1. **Departure delay-risk prediction** on 7.1 million public BTS 2024 flight legs.
2. **Engine remaining-useful-life (RUL) prediction** on NASA C-MAPSS FD001.
3. **CP-SAT maintenance planning** that assigns aircraft to hangar slots under
   capacity, check-type, ground-time and RUL due-date constraints.
4. **A Streamlit decision app** with six pages, including a live ADS-B traffic map,
   each ending in a plain-language recommendation.

Every number in this repository is produced by the code from public or explicitly
synthetic data. There are no invented results, no proprietary airline data, and no
claim of access to Airbus Skywise or any airline system.

---

## Results snapshot

| Area | Result | Honest reading |
|---|---|---|
| OTP model (LightGBM) | ROC-AUC **0.818**, PR-AUC **0.661**, Brier **0.097**, ECE **0.019** | Strong ranking and good calibration on a strictly later test period. |
| OTP baselines | Route-hour rule ROC-AUC **0.625**; logistic ROC-AUC **0.747** | The GBM adds real lift over both. |
| Top-decile targeting | **4.45x** lift, **44.5%** of delays captured, **72.5%** precision at the recommended threshold | Useful for a finite OCC team; not a guarantee. |
| RUL models | Ridge RMSE **21.4**; XGBoost **14.0**; LSTM **15.4** cycles | Window models beat the last-cycle baseline; LSTM is not the best on this split. |
| RUL alert policy | 20-cycle rule: precision **88.2%**, recall **93.8%** (15 TP, 2 FP, 1 FN) | Cost-optimal threshold is 19 cycles with the same outcome. |
| Conformal interval | **+/-22.6** cycles, empirical coverage **83%** at a 90% target | Interval is slightly narrow on 100 test engines. |
| Maintenance optimization | **$645k** optimized vs **$1.198M** current practice, **46.1%** saving | Synthetic 15-aircraft instance; deterministic and solver-proven optimal. |
| Statistical tests | Afternoon vs morning delay **28.6% vs 10.7%**, p < 2.2e-16 | Associational; see the R notebook. |

These are prototype metrics. They are not production SLAs and they are not audited
financial savings.

---

## Quick start

### 1. Environment

Python 3.11+ is recommended (the development environment used Python 3.12).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

R 4.x with `rmarkdown`, `knitr` and `survival` is optional, only for the statistical
notebook. Pandoc is required to render the R Markdown to HTML.

### 2. Build data

The raw files are cached under `data/raw`. If they are absent, the loaders download
the public BTS monthly zips and the C-MAPSS mirror.

```powershell
python scripts/build_data.py
```

### 3. Train and score

```powershell
python scripts/train_otp.py          # ~6 minutes on the full 7.1M rows
python scripts/train_rul.py          # ~35 seconds on CPU
python scripts/run_optimization.py   # <1 second for the 15-aircraft instance
python scripts/export_analysis_tables.py
```

Or run the whole pipeline:

```powershell
python scripts/run_all.py --force
```

Reuse existing artifacts without retraining:

```powershell
python scripts/run_all.py --skip-data --skip-train
```

### 4. Launch the app

```powershell
python -m streamlit run app.py
```

Open <http://localhost:8501>. The app has six pages:

- **OTP overview** - network, time-of-day, seasonality, turnaround, SHAP and model metrics.
- **Live flight tracking** - current ADS-B positions from the OpenSky Network, with region, movement, altitude and callsign filters, a map, an aircraft table and a detail panel.
- **Delay-risk predictor** - score one flight and inspect its local SHAP drivers.
- **Fleet health & RUL** - engine watchlist, alert economics, conformal intervals and drill-down.
- **Maintenance optimizer** - CP-SAT schedule, cost breakdown and due-date risk.
- **Scenario comparison** - hangar closure, RUL stress, demand surge and AOG-penalty sensitivity.

Live tracking calls the public OpenSky API from the app. Anonymous access is rate
limited; set `OPENSKY_ACCESS_TOKEN`, or `OPENSKY_CLIENT_ID` and
`OPENSKY_CLIENT_SECRET`, to use OAuth2 and raise the limit. If OpenSky is
unreachable the page shows a clearly labelled demo snapshot instead of failing.
The data is crowd-sourced and is **not** surveillance-grade.

### 5. Test

```powershell
python -m pytest
```

GitHub Actions runs the same suite on Python 3.11 and 3.12 for every push and pull
request to `main` (`.github/workflows/ci.yml`). CI installs only the test
dependencies, so it does not download model or data artifacts.

---

## Repository layout

```
config.yaml                     All thresholds, costs, seeds and paths
app.py                          Streamlit launcher (adds src/ to sys.path)
src/flightops/
  config.py                     Typed config, path resolution, seeding
  data/                         BTS and C-MAPSS ingestion, contracts, DuckDB
  features/                     Leakage-safe OTP and RUL feature builders
  models/                       OTP training/calibration, RUL models, serving
  optimization/                 Synthetic fleet, CP-SAT planner, scenarios
  analysis/                     OTP EDA, RCA narrative, figures
  live/                         OpenSky ADS-B fetch, parsing, filters, demo fallback
  app/                          Streamlit shell, data access, six pages
  sql/                          DuckDB schema and named analytics queries
scripts/                        Reproducible pipeline and export entrypoints
notebooks/                      01 OTP EDA, 02 RUL, 03 optimization, 04 R statistics
tests/                          Feature, contract and optimizer constraint tests
docs/                           Architecture, data model, model cards, production plan
deployment/                     Vertex AI and Databricks deployment assets
reports/                        Generated metrics, figures and optimization outputs
artifacts/                      Trained models and encoders
```

---

## Data provenance

| Dataset | Source | Use | Caveat |
|---|---|---|---|
| BTS On-Time Performance 2024 | U.S. Bureau of Transportation Statistics, public monthly zips | OTP model and EDA | U.S. domestic carriers only; local airport timestamps; no equipment field. |
| NASA C-MAPSS FD001 | NASA PCoE, public mirror `edwardzjl/CMAPSSData` | RUL models | Simulated turbofan degradation; one operating condition and one fault mode. |
| Aircraft type | Deterministic synthetic enrichment | OTP feature and app grouping | BTS has no equipment field; FAA registry was unavailable (HTTP 503). Validate against a real fleet registry. |
| Live aircraft positions | OpenSky Network public ADS-B API | Live tracking page | Crowd-sourced and uneven coverage; positions may lag; rate limited; not surveillance-grade. |
| Maintenance fleet and hangars | Synthetic, seeded from `config.yaml` | CP-SAT planner | 15 aircraft, 52 slots, 3 hangars. Not an airline schedule. |
| Cost assumptions | Documented planning assumptions in `config.yaml` | Business framing | Not audited P&L figures. |

The BTS extract is public domain. C-MAPSS is a public NASA research dataset; check
the upstream terms before redistribution. No raw data is committed to this repo
when the files are large; the scripts recreate them.

---

## Modelling summary

### OTP delay risk

- **Target:** `dep_del15` (departure delay >15 minutes).
- **Split:** train Jan-Aug 2024, validation Sep-Oct, test Nov-Dec. Strictly time-based.
- **Cutoff:** T-90 minutes before scheduled departure. Inbound actuals are used only
  when observable by then (`inbound_known`).
- **Models:** route-hour smoothed baseline, logistic regression, LightGBM with early
  stopping, isotonic calibration fitted on validation only.
- **Evaluation:** ROC-AUC, PR-AUC, Brier, ECE, precision/recall, calibration curve,
  top-decile lift and capture.
- **Explainability:** global and local SHAP, grouped into plain-language driver groups.
- **Threshold:** the recommended operating point is the validation top-decile
  threshold (0.3031). The cost-optimal validation threshold (0.05) is reported for
  comparison but flags far too many flights for a finite team.

### RUL

- **Target:** capped RUL (125 cycles) from 30-cycle sensor windows.
- **Split:** 80/20 by engine unit, never by row.
- **Models:** Ridge last-cycle baseline, XGBoost window summary, PyTorch LSTM.
- **Evaluation:** RMSE, MAE, R2, asymmetric NASA score, late-prediction rate, P90
  absolute error and conformal coverage.
- **Alert policy:** fixed safety event `true RUL <= 20`; compare early-removal cost
  against unscheduled-removal cost. Costs are configurable planning assumptions.

### Maintenance optimization

- **Decision:** assign each aircraft needing maintenance to a hangar slot.
- **Hard constraints:** one aircraft per hangar-day, fleet eligibility, A/C-check
  type, minimum ground time, horizon.
- **Objective:** flying disruption + early-maintenance waste + escalating AOG-risk
  penalty + unassigned penalty.
- **Scenarios:** baseline, H3 closure, +25% RUL stress, +10% demand surge.
- **Sensitivity:** AOG-risk penalty from $15k to $60k per aircraft-day.

See [docs/optimization_model.md](docs/optimization_model.md) for the full formulation.

---

## Decision-support framing

The app is deliberately written as decision support, not automation:

- OTP predictions **rank** flights for a human recovery team. They do not cancel,
  delay or re-crew anything.
- RUL predictions are a **watchlist**. A licensed engineer must approve any removal,
  deferral or release-to-service decision.
- The optimizer produces a **proposed plan**. CAMO planners can override it; every
  override should be logged with a reason for model monitoring.

Each app page ends with a "So what / recommended action" box. The recommendations are
written to be actionable and to state their assumptions.

---

## Limitations and assumptions

Read [docs/limitations.md](docs/limitations.md) before using any result. The short
version:

- Observational associations, not causal effects.
- BTS local timestamps are kept naive; no cross-airport UTC conversion.
- Aircraft type is synthetic; weather is only proxied by calendar and season.
- C-MAPSS is simulated and run-to-failure; real fleets have censoring, repairs and
  mixed operating regimes.
- Maintenance costs, fleet, slots and disruption penalties are planning assumptions.
- The prototype has no Skywise, airline, OEM or MRO integration.
- Model performance will degrade under fleet, network, schedule and policy drift.

---

## Documentation

- [Tutorial guide](docs/TUTORIAL_GUIDE.md)
- [Technical documentation](docs/TECHNICAL_DOCUMENTATION.md)
- [Architecture](docs/architecture.md)
- [Data model and contracts](docs/data_model.md)
- [OTP model card](docs/model_cards/otp_model_card.md)
- [RUL model card](docs/model_cards/rul_model_card.md)
- [Optimization model card](docs/model_cards/optimization_model_card.md)
- [Optimization formulation](docs/optimization_model.md)
- [Limitations and assumptions](docs/limitations.md)
- [Production mapping: Vertex AI, Databricks and Skywise governance](docs/production_mapping.md)
- [Executive summary](docs/executive_summary.md)
- [Statistical analysis (R)](notebooks/04_statistical_analysis.html)

---

## Reproducibility

- All stochastic components use `project.seed` (default 42).
- Splits are fixed by month or engine unit and documented in `config.yaml`.
- Costs, thresholds, hyper-parameters and scenario perturbations live in `config.yaml`.
- Metrics, predictions and figures are regenerated by the scripts; the app reads only
  generated artifacts.
- Unit tests cover feature engineering, data contracts and optimizer constraints.

## Status

Prototype, not production. The code is typed and tested where it matters, but it has
not been through airline safety, security, model-risk or data-governance review.
