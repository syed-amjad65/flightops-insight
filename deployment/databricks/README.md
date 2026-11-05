# Databricks deployment runbook

## 1. Prerequisites

- Databricks workspace with Unity Catalog enabled.
- A catalog and schema for the project (e.g. `flightops.gold`).
- A Unity Catalog Volume or cloud storage path for raw/processed data and wheels.
- Cluster policy allowing the required node types.
- Databricks CLI authenticated to the target workspace.

## 2. Build the wheel

```bash
python -m build
# upload dist/flightops_insight-0.1.0-py3-none-any.whl to the Volume in databricks.yml
```

## 3. Configure the bundle

Edit `databricks.yml`:

- `workspace.host`
- `variables.catalog`
- `variables.wheel_path`
- notification email addresses

Then validate and deploy:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
```

## 4. Data layout

Recommended Unity Catalog layout:

```
flightops/
  bronze/
    flights_raw/
    cmapss_raw/
  silver/
    flights/
    cmapss/
  gold/
    otp_features/
    otp_scores/
    rul_watchlist/
    maintenance_plan/
```

Use Delta Lake for all tables. Partition large tables by `flight_date` month and
maintain statistics.

## 5. Jobs

The bundle defines:

- `flightops-train-otp` (weekly)
- `flightops-train-rul` (monthly)
- `flightops-optimize-maintenance` (daily)
- `flightops-score-otp` (hourly)

Add a `flightops-score-rul` job for weekly batch inference. Add a monitoring job that
writes calibration and drift tables.

## 6. MLflow

Each training run should log:

- params from `config.yaml`;
- metrics (ROC-AUC, PR-AUC, Brier, ECE, RMSE, NASA score, coverage);
- model artifacts and encoders;
- the model card and SHAP summary;
- dataset fingerprint and split definition.

Register the model in Unity Catalog and promote with aliases (`staging`, `production`).

## 7. Serving

Use Databricks Model Serving for online OTP scoring if the airline standardizes on
Databricks. Otherwise deploy the same artifact to Vertex AI. Do not run two production
serving paths for the same decision without a documented fallback.

## 8. Governance

- Unity Catalog grants and row/column masks.
- Lineage from Bronze to Gold to model to decision.
- Audit logs exported to the SIEM.
- Human approval for maintenance actions.
- No direct writes to MRO release records.

## 9. What this template does not do

- It does not create catalogs, schemas, volumes or clusters.
- It does not include secrets or workspace tokens.
- It does not call Skywise or airline APIs.
- It does not guarantee performance; retrain and validate on airline data.
