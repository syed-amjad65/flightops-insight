# Model card: engine remaining useful life (RUL)

## 1. Model details

| Field | Value |
|---|---|
| Name | C-MAPSS FD001 RUL models (Ridge, XGBoost, LSTM) |
| Version | 0.1.0 prototype |
| Type | Regression with conformal prediction interval and alert policy |
| Target | Remaining useful life in flight cycles, capped at 125 |
| Owner (prototype) | FlightOps Insight |
| Intended users | CAMO engineers, maintenance planners, reliability engineers |
| Decision supported | Prioritize engine inspection and shop visits; not release-to-service authority |

## 2. Intended use

The model estimates engine RUL from the last 30 cycles of sensor data and produces:

- a point estimate in cycles;
- a 90% conformal interval;
- a risk tier (Critical, High, Watch, Serviceable);
- a recommended action for CAMO review.

It is a **watchlist and prioritization aid**. It does not authorize removal, deferral
or continued operation.

## 3. Out-of-scope use

Do not use this model to:

- release an engine to service or approve a deferral;
- replace borescope, oil analysis, vibration analysis or OEM limits;
- predict a specific failure mode or remaining time to a specific component failure;
- operate on a fleet without recalibration and validation;
- make a safety-critical decision without a licensed engineer.

## 4. Training data

| Field | Value |
|---|---|
| Source | NASA C-MAPSS FD001, public mirror `edwardzjl/CMAPSSData` |
| Subset | FD001: one operating condition, one fault mode (HPC degradation) |
| Train engines | 100 run-to-failure trajectories |
| Test engines | 100 truncated trajectories with NASA ground-truth RUL |
| Rows | 33,727 |
| Split | 80/20 by engine unit (80 train, 20 validation) |
| Window | 30 cycles |
| RUL cap | 125 cycles |
| Sensors | 15 informative channels after dropping constant channels |

C-MAPSS is **simulated** turbofan degradation. It is not an airline fleet feed, and
the trajectories do not include repairs, mixed operating regimes or real censoring.

## 5. Features

The models use 15 sensor channels selected by variance and relevance:

`sensor_2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14, 15, 17, 20, 21`.

Constant channels (`sensor_1, 5, 10, 16, 18, 19`) are dropped. FD001 has a single
operating condition, so the three operating settings are not informative.

- **Ridge baseline:** last-cycle sensor values only.
- **XGBoost:** summary statistics over the 30-cycle window (mean, slope, min, max,
  last value, etc.).
- **LSTM:** raw 30 x 15 sequence, scaled with training statistics.

## 6. Model family and training

| Model | Role | Configuration |
|---|---|---|
| Ridge | Last-cycle baseline | Regularized linear regression |
| XGBoost | Window-summary model | 500 trees max, depth 4, learning rate 0.04, early stopping |
| LSTM | Sequence model | 2 layers, hidden size 64, dropout 0.2, 80 epochs max, early stopping |

Random seed: 42. XGBoost best iteration: 135. LSTM early-stopped at epoch 17 in the
latest run.

## 7. Performance (100 test engines)

| Metric | Ridge last-cycle | XGBoost window | LSTM |
|---|---:|---:|---:|
| RMSE (cycles) | 21.36 | **13.999** | 15.366 |
| MAE (cycles) | 17.132 | **10.448** | 11.549 |
| R2 | 0.7358 | **0.8865** | 0.8633 |
| NASA score (mean) | 10.692 | **2.860** | 3.602 |
| Late-prediction rate | 56% | 61% | **46%** |
| P90 absolute error | 35.478 | **23.535** | 25.301 |

**Reading.** XGBoost is the best point predictor on this split. The LSTM has a lower
late-prediction rate, which matters for safety, but a higher RMSE. The prototype keeps
all three for comparison and uses the LSTM for the watchlist because its alert policy
is evaluated on LSTM predictions.

## 8. Conformal interval

A split-conformal interval is fitted on validation residuals:

- Target coverage: 90%.
- Interval: **+/-22.6 cycles**.
- Empirical test coverage: **83%**.

The interval is slightly narrower than the target on 100 test engines. In production,
recalibrate on a larger, fleet-representative calibration set and report coverage by
operating regime and sensor health.

## 9. Alert policy and cost trade-off

The safety event is `true RUL <= 20 cycles`.

| Threshold | TP | FP | FN | TN | Precision | Recall | Cost |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 19 cycles (cost-optimal) | 15 | 2 | 1 | 82 | 88.2% | 93.8% | $1.525M |
| **20 cycles (selected)** | **15** | **2** | **1** | **82** | **88.2%** | **93.8%** | **$1.525M** |

Cost assumptions:

- Unscheduled removal: **$250,000**.
- Planned early removal: **$75,000**.

Benchmarks:

- No alerts: **$4.0M** (16 missed events x $250k).
- Remove all engines early: **$7.5M** (100 x $75k).
- Selected policy: **$1.525M**.
- Savings versus no alerts: **$2.475M**.
- Savings versus remove-all: **$5.975M**.

These are illustrative planning assumptions, not audited maintenance savings. The
cost-optimal threshold is flat around 19-20 cycles because the same 17 engines are
flagged.

## 10. Explainability and diagnostics

- Cost curve: `reports/figures/rul_alert_cost.png`.
- Predicted vs true: `reports/figures/rul_predicted_vs_true.png`.
- Error histogram and cost curve: `reports/figures/rul_alert_cost.png`.
- Training history: `reports/figures/rul_training_history.png`.
- Representative trajectories: `reports/figures/rul_trajectories.png`.

The model does not currently provide per-prediction sensor attributions. A production
version should add SHAP or integrated gradients for the selected model.

## 11. Limitations and risks

- C-MAPSS is simulated; real engines have repairs, maintenance actions and mixed
  operating conditions.
- FD001 has one fault mode; the model will not generalize to other failure modes.
- The test set has 100 engines; metrics have wide uncertainty.
- The RUL cap at 125 compresses early-life predictions.
- Conformal coverage is 83%, below the 90% target.
- Alert costs are assumptions; a real airline must supply its own cost model.
- The model cannot distinguish "RUL 20 cycles" from "RUL 20 cycles but unsafe now".
- No integration with borescope, oil analysis or OEM limits.

## 12. Monitoring and retraining

Track:

- RMSE, MAE, NASA score and late-prediction rate on labelled removals;
- conformal coverage and interval width;
- alert rate, precision, recall and missed critical events;
- sensor drift and missing channels;
- risk-tier distribution and CAMO override rate;
- time from alert to shop visit;
- unscheduled removal rate and AOG events.

Retrain when a sufficient number of new run-to-failure or teardown labels accumulate,
and after engine type, operating regime or maintenance-programme changes. Use a
shadow period and engineer sign-off before promotion.

## 13. Ethical and operational notes

- RUL is a decision aid, not a release-to-service authority.
- Every removal, deferral and continued-operation decision must be signed off by a
  licensed engineer under the approved maintenance programme.
- Present the interval, not just the point estimate.
- Log overrides and near-misses for model review.
- Do not use the model to pressure engineers to defer maintenance.

## 14. Artifacts

- `artifacts/models/rul_ridge.joblib`
- `artifacts/models/rul_xgboost.joblib`
- `artifacts/models/rul_lstm.pt`
- `artifacts/models/rul_lstm_config.pt`
- `artifacts/encoders/rul_scaler.json`
- `artifacts/encoders/rul_features.json`
- `reports/metrics/rul_metrics.json`
- `reports/metrics/rul_watchlist.csv`
- `reports/metrics/rul_alert_cost.csv`
