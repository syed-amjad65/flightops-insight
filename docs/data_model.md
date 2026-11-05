# Data model and contracts

## 1. Canonical flight table

The OTP pipeline writes one row per scheduled flight leg to
`data/processed/otp_flights.parquet` (7,079,059 rows, 51 columns in the 2024 build).
Every provider must emit the columns required by `OTP_REQUIRED_COLUMNS` in
`src/flightops/data/contracts.py`; otherwise the contract raises before training.

### 1.1 Identity and schedule

| Column | Type | Meaning |
|---|---|---|
| `flight_date` | timestamp | Local scheduled departure date. Required and non-null. |
| `month` | int | Month 1-12, derived from `flight_date`. |
| `day_of_week` | int | ISO weekday 1-7 (Monday = 1). |
| `day_of_year` | int | Day 1-366, used for seasonality features. |
| `carrier` | string | Marketing carrier code (e.g. `WN`, `DL`). |
| `tail_number` | string | Aircraft tail; may be null in the raw feed. |
| `flight_number` | int | Carrier flight number. |
| `origin`, `dest` | string | IATA airport codes. |
| `origin_state`, `dest_state` | string | U.S. state codes; used by the feature builder. |
| `sched_dep_ts`, `sched_arr_ts` | timestamp | Local scheduled departure/arrival. |
| `dep_hour`, `dep_minute` | int | Local scheduled departure clock time. |

**Time semantics.** BTS timestamps are local airport times. The prototype keeps them
naive and derives durations within a flight leg. It does not convert across airports
to UTC, which would be required for a globally distributed fleet.

### 1.2 Outcome fields (never model inputs)

| Column | Type | Meaning |
|---|---|---|
| `dep_delay_min` | float | Actual departure delay in minutes (negative = early). |
| `dep_del15` | 0/1 | 1 if departure delay >15 minutes; the OTP target. |
| `arr_delay_min` | float | Actual arrival delay. |
| `arr_del15` | 0/1 | 1 if arrival delay >15 minutes. |
| `cancelled`, `diverted` | 0/1 | Cancellation and diversion flags. |
| `carrier_delay_min`, `weather_delay_min`, `nas_delay_min`, `security_delay_min`, `late_aircraft_delay_min` | float | Post-flight attribution. Excluded from features. |

The feature builder receives the full frame but only selects the allowed feature
columns. This is a deliberate leakage guard: post-flight attributions and actual
outcomes cannot enter the model.

### 1.3 Aircraft enrichment (synthetic)

| Column | Type | Meaning |
|---|---|---|
| `aircraft_type` | string | Synthetic type code (e.g. `A320`, `B738`, `E175`). |
| `aircraft_family` | string | `narrowbody`, `regional`, `widebody`. |
| `seats` | int | Representative seat count for the type. |
| `min_turn_min` | int | Representative minimum turnaround for the type. |

BTS has no equipment field. `src/flightops/data/aircraft.py` assigns a type
deterministically from the tail number and carrier fleet mix. The assignment is
documented, stable across runs, and **not** an airline fleet record. Validate it
against a real fleet registry before operational use.

### 1.4 Turnaround and network context

| Column | Type | Meaning |
|---|---|---|
| `sched_turn_min` | int | Scheduled ground time from the inbound aircraft's arrival to this departure. Null if no inbound link. |
| `turnaround_buffer_min` | int | `sched_turn_min - min_turn_min`; derived. |
| `prev_dep_delay_min` | float | Inbound aircraft's departure delay, if known. |
| `prev_arr_delay_obs` | float | Inbound aircraft's arrival delay, if observed by the cutoff. |
| `inbound_known` | 0/1 | 1 only when the inbound actual was observable at T-90 minutes. |
| `prev_cancelled` | 0/1 | Whether the inbound leg was cancelled. |
| `origin_hour_departures` | int | Departures at the origin in the scheduled hour. |
| `origin_daily_departures` | int | Departures at the origin that day. |
| `route_daily_frequency` | int | Flights on the same route that day. |
| `is_hub_origin`, `is_hub_dest` | 0/1 | Carrier-hub indicators. |
| `is_holiday`, `is_weekend` | 0/1 | Calendar flags. |

**Leakage rule.** `prev_arr_delay_obs` is populated only when `inbound_known = 1`.
At scoring time, the app asks whether the inbound aircraft has already arrived; if
not, the value is set missing. This encodes the T-90 cutoff in the data, not just in
documentation.

## 2. OTP feature vector

The trained LightGBM model uses 37 features:

- **Numeric (28):** month, day_of_week, day_of_year, dep_hour, dep_minute, is_weekend,
  is_holiday, distance_mi, crs_elapsed_min, seats, min_turn_min, sched_turn_min,
  turnaround_buffer_min, prev_dep_delay_min, prev_arr_delay_obs, inbound_known,
  has_turnaround, prev_cancelled, origin_hour_departures, origin_daily_departures,
  route_daily_frequency, is_hub_origin, is_hub_dest, is_peak, hour_sin, hour_cos,
  doy_sin, doy_cos.
- **Categorical (9):** carrier, origin, dest, route, aircraft_type, aircraft_family,
  origin_state, dest_state, dep_time_block.

The feature builder serializes its category vocabulary and route-hour rates to
`artifacts/encoders/otp_feature_builder.json`. The same builder is used for training,
batch scoring and the app, which prevents train/serve skew.

## 3. RUL table

`data/processed/cmapss_fd001.parquet` contains 33,727 rows: 100 training engines and
100 test engines, each with 3 operating settings and 21 sensor channels.

| Column | Type | Meaning |
|---|---|---|
| `unit` | int | Engine unit identifier. Split by unit, never by row. |
| `cycle` | int | Flight cycle, starting at 1. |
| `split` | string | `train` or `test`. |
| `rul` | float | Remaining useful life in cycles. Training RUL is computed from the run-to-failure end; test RUL is the NASA ground truth at the last observed cycle. |
| `op_setting_1..3` | float | Operating conditions. Constant in FD001; excluded from the selected sensor set. |
| `sensor_1..21` | float | Sensor channels. Constant channels are dropped before modelling. |

The 15 informative sensors used by the models are listed in
`reports/metrics/rul_metrics.json` and `artifacts/encoders/rul_features.json`.

### 3.1 RUL windowing

- Window length: 30 cycles.
- RUL cap: 125 cycles, so late-life predictions are not dominated by very large labels.
- Scaling: fitted on training windows only, persisted as JSON.
- Split: 80/20 by engine unit (`rul.val_fraction = 0.20`).
- Test: the official 100 truncated test engines, scored once at their last window.

## 4. Maintenance optimization data model

The optimizer uses a synthetic instance generated by
`src/flightops/optimization/synthetic_fleet.py` from `project.seed`.

### 4.1 Aircraft due list (`instance_aircraft.csv`)

| Column | Meaning |
|---|---|
| `tail`, `fleet`, `base` | Aircraft identity and home base. |
| `due_day`, `due_date` | Earliest binding due date inside the horizon. |
| `binding_driver` | `A-check interval`, `C-check interval` or `Engine RUL`. |
| `check_type` | `A` or `C` check required. |
| `hours_since_a`, `hours_since_c` | Hours since the last check. |
| `engine_rul_cycles` | Engine RUL at the start of the horizon. |
| `a_due_day`, `c_due_day`, `engine_due_day` | Component due days before the minimum. |

### 4.2 Hangar slots (`instance_slots.csv`)

| Column | Meaning |
|---|---|
| `slot_id` | Stable slot identifier, e.g. `H1-C-D06`. |
| `hangar`, `base` | Hangar and base. |
| `start_day`, `start_date` | Planning day and calendar date. |
| `duration_days` | 1 for A-check, 2 for C-check. |
| `check_type` | `A` or `C`. |
| `ground_time_h` | Available ground time. |
| `eligible_fleets` | Comma-separated fleet list. |
| `days_covered` | Hangar-days consumed. |

### 4.3 Plan output (`maintenance_plan_optimized.csv`)

| Column | Meaning |
|---|---|
| `tail`, `fleet`, `base` | Aircraft. |
| `check_type`, `binding_driver`, `due_day` | Demand. |
| `assigned_slot`, `hangar`, `slot_start_day` | Decision. |
| `late_days`, `early_days` | Deviation from due date. |
| `disruption_usd`, `early_maintenance_usd`, `aog_risk_usd`, `unassigned_penalty_usd` | Cost components. |
| `total_cost_usd` | Sum of components. |
| `status` | `Scheduled` or `Unassigned`. |

## 5. Analytics database

`data/processed/flightops.duckdb` is built from the flight Parquet and the generated
CSV artifacts. It exposes:

- **Views:** `dim_date`, `dim_carrier`, `dim_airport`, `dim_aircraft`, `dim_route`,
  `fleet_watchlist`, `maintenance_plan`.
- **Tables:** `otp_overview`, `otp_daily`, `otp_monthly`, `otp_hour`, `otp_carrier`,
  `otp_aircraft`, `otp_route`, `turnaround_effect`, `delay_cause_mix`.

These tables make the app fast and give analysts a SQL surface without loading the
332 MB Parquet into memory.

## 6. Artifacts and metrics

| Artifact | Location | Consumer |
|---|---|---|
| LightGBM booster | `artifacts/models/otp_lightgbm.txt` | App, batch scoring |
| Logistic model | `artifacts/models/otp_logistic.joblib` | Benchmark |
| Isotonic calibrators | `artifacts/models/otp_calibrator_*.joblib` | App, batch scoring |
| OTP feature vocabulary | `artifacts/encoders/otp_feature_builder.json` | App, batch scoring |
| LSTM weights/config | `artifacts/models/rul_lstm.pt`, `rul_lstm_config.pt` | RUL scoring |
| XGBoost/Ridge RUL | `artifacts/models/rul_*.joblib` | RUL scoring |
| RUL scaler/features | `artifacts/encoders/rul_scaler.json`, `rul_features.json` | RUL scoring |
| OTP metrics | `reports/metrics/otp_metrics.json` | App, docs |
| OTP insights | `reports/metrics/otp_insights.json` | App, docs |
| RUL metrics | `reports/metrics/rul_metrics.json` | App, docs |
| RUL watchlist | `reports/metrics/rul_watchlist.csv` | App |
| Alert cost table | `reports/metrics/rul_alert_cost.csv` | App, docs |
| Optimization results | `reports/optimization/scenario_results.json` | App, docs |

## 7. Data quality rules

The contracts enforce:

- Required columns present.
- Non-empty frames unless explicitly allowed.
- `flight_date` non-null.
- `dep_del15` binary.
- `cancelled` binary.
- `unit` non-null for RUL.
- `cycle >= 1` for RUL.

Additional guards live in the feature builders:

- Route-hour rates are fitted on training months only.
- Unseen categories map to `__UNSEEN__`.
- Test months are never used for fitting or calibration.
- RUL windows never cross engine units.
- Scaling statistics come from training data only.

## 8. Known data gaps

- No station-level weather feed; season and calendar are proxies.
- No crew, gate, aircraft rotation or passenger load data.
- No maintenance work orders, parts or borescope records.
- No airline P&L data; all cost figures are configurable planning assumptions.
- BTS data are U.S. domestic only and do not represent an international network.
