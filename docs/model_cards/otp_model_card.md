# Model card: OTP departure delay risk

## 1. Model details

| Field | Value |
|---|---|
| Name | OTP departure delay risk (LightGBM, isotonic-calibrated) |
| Version | 0.1.0 prototype |
| Type | Binary classification with probability calibration |
| Target | `dep_del15`: departure delay >15 minutes |
| Owner (prototype) | FlightOps Insight |
| Intended users | OCC duty managers, network controllers, dispatch and data science reviewers |
| Decision supported | Rank flights for recovery intervention; not automate departure decisions |

## 2. Intended use

The model scores a flight **90 minutes before scheduled departure** and returns a
calibrated probability that it will depart more than 15 minutes late. It is used to:

- rank flights for a finite OCC intervention team;
- explain the main drivers of a specific flight's risk with local SHAP values;
- compare risk across time of day, route, carrier and turnaround buffer;
- support "what if" analysis on inbound delay and scheduled turnaround.

## 3. Out-of-scope use

Do not use this model to:

- cancel, delay or re-crew a flight automatically;
- make safety or airworthiness decisions;
- infer causality ("short turns cause delays");
- evaluate individual employees or carriers;
- score non-U.S. or non-BTS-compatible operations without retraining;
- score flights where the prediction cutoff is not enforced.

## 4. Training data

| Field | Value |
|---|---|
| Source | U.S. Bureau of Transportation Statistics On-Time Performance, 2024, all 12 months |
| Rows | 7,079,059 scheduled flight legs |
| Train | Jan-Aug 2024: 4,621,706 rows |
| Validation | Sep-Oct 2024: 1,186,776 rows |
| Test | Nov-Dec 2024: 1,156,765 rows |
| Positive rate | 22.86% train, 18.02% test |
| Target | `dep_del15` |
| Excluded | Post-flight delay-cause attributions and actual outcomes |

Aircraft type is a **synthetic enrichment** because BTS has no equipment field. It is
assigned deterministically from tail number and carrier fleet mix. It is not an
airline fleet record.

## 5. Features

37 features: 28 numeric and 9 categorical.

- Schedule: month, day of week, day of year, scheduled hour and minute, weekend,
  holiday, departure time block, peak indicator, cyclical time/season encodings.
- Flight: distance, scheduled block time, seats, minimum turnaround.
- Turnaround: scheduled ground time, buffer above minimum, inbound departure delay,
  inbound arrival delay (only when observed by the cutoff), inbound-known flag,
  inbound cancellation.
- Network: origin/destination, route, states, carrier, carrier-hub flags, departures
  at origin in the hour and day, route daily frequency.
- Aircraft: synthetic type and family.

The feature builder persists its category vocabulary and route-hour rates. The same
builder is used in training, batch scoring and the app.

## 6. Cutoff and leakage controls

- **Prediction cutoff:** T-90 minutes.
- **Inbound actuals:** only used when `inbound_known = 1`, meaning the inbound
  aircraft had arrived or its actual was observable by the cutoff.
- **Post-flight fields:** carrier/weather/NAS/security/late-aircraft delay minutes are
  excluded.
- **Fitting discipline:** route-hour rates, encoders, LightGBM and calibrators are
  fitted without test months. Isotonic calibration uses validation only.
- **Evaluation:** test months are strictly later than validation months.

## 7. Model family and training

| Model | Role | Configuration |
|---|---|---|
| Route-hour smoothed rate | Baseline | Minimum 30 flights per route-hour; pooled otherwise |
| Logistic regression | Interpretable baseline | `C=0.5`, `saga`, 750k-row training subsample |
| LightGBM | Main model | 600 trees max, learning rate 0.05, 127 leaves, early stopping |
| Isotonic calibration | Probability calibration | Fitted on validation predictions only |

Random seed: 42. Best LightGBM iteration: 177.

## 8. Performance (held-out Nov-Dec 2024)

| Metric | Route-hour baseline | Logistic regression | LightGBM |
|---|---:|---:|---:|
| ROC-AUC | 0.6246 | 0.7471 | **0.8182** |
| PR-AUC | 0.2493 | 0.5226 | **0.6610** |
| Brier score | 0.1451 | 0.1165 | **0.0965** |
| ECE | 0.0376 | 0.0420 | **0.0189** |
| Precision @0.5 | 0.000 | 0.799 | 0.853 |
| Recall @0.5 | 0.000 | 0.281 | 0.405 |
| Top-decile lift | 1.61x | 3.77x | **4.45x** |
| Top-decile capture | 16.1% | 37.7% | **44.5%** |

At the recommended top-decile threshold (0.3031):

- Flag rate on test: 12.2% (141,762 flights).
- Precision: 72.5%.
- Recall: 49.3%.
- Average delay among flagged true positives: 81.4 minutes.

Calibration is good: ECE 0.019 and Brier 0.097. The model is better at ranking than
at extracting every delay at a high-precision threshold.

## 9. Threshold policy

Two thresholds are reported:

- **Recommended operating threshold: 0.3031** (validation top-decile). It matches a
  realistic OCC capacity of about 10% of flights and gives 72.5% precision.
- **Cost-optimal validation threshold: 0.05.** It minimizes an assumed cost function
  but flags 842,646 validation flights, which is not operationally usable. It is
  reported for transparency, not recommended for the app.

The threshold should be chosen with the OCC, based on team capacity and the relative
cost of a missed delay versus an unnecessary intervention.

## 10. Explainability

SHAP TreeExplainer is used globally and locally. Grouped importance:

| Driver group | Share of mean absolute SHAP |
|---|---:|
| Turnaround & inbound aircraft | 55.6% |
| Airport & network | 15.0% |
| Time of day | 11.9% |
| Season & calendar | 11.1% |
| Carrier | 4.5% |
| Aircraft & route | 2.0% |

Top individual features: inbound departure delay, scheduled turnaround, inbound
arrival delay, time-of-day sine, seasonal cosine, carrier, route, origin.

Local explanations are shown in the app. They are **associational contributions**,
not causal effects.

## 11. Limitations and risks

- Observational data; no causal identification.
- No weather feed; season and calendar are proxies.
- Aircraft type is synthetic.
- BTS covers U.S. domestic operations only.
- Carrier and route effects may encode schedule and network structure, not
  operational quality.
- Calibration may drift under schedule, fleet, weather or policy changes.
- The business case assumes a prevention rate; it is not a measured treatment effect.
- No protected-class fairness audit is possible because BTS does not include them.
- The model does not know about crew, gate, maintenance or passenger connections.

## 12. Monitoring and retraining

Production monitoring should track:

- input feature distributions and missingness, especially `inbound_known`;
- calibration (ECE, reliability curve) on rolling windows;
- ROC-AUC, PR-AUC and top-decile precision/capture;
- flag rate and intervention rate versus OCC capacity;
- override rate and reason codes;
- segment performance by carrier, station, route and time of day;
- data freshness and late-arriving actuals.

Retrain at least quarterly, and sooner after schedule, fleet, airport or policy
changes. Use a time-based backtest and a shadow period before promotion.

## 13. Ethical and operational notes

- The model must not be used to penalize individual crews or dispatchers.
- Predictions should be presented with uncertainty and the main drivers.
- OCC staff must be able to override without friction, and overrides should be logged.
- The model should not be the sole basis for a passenger-facing communication.

## 14. Artifacts

- `artifacts/models/otp_lightgbm.txt`
- `artifacts/models/otp_logistic.joblib`
- `artifacts/models/otp_calibrator_lightgbm.joblib`
- `artifacts/models/otp_calibrator_logistic_regression.joblib`
- `artifacts/models/otp_calibrator_route_hour_baseline.joblib`
- `artifacts/encoders/otp_feature_builder.json`
- `reports/metrics/otp_metrics.json`
- `reports/metrics/otp_insights.json`
- `reports/metrics/otp_test_predictions.parquet`
