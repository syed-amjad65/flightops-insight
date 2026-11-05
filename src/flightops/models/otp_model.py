"""Train, calibrate and explain the OTP delay-risk models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.metrics import precision_recall_curve, roc_curve

from flightops.config import Config, ensure_directories, set_global_seed
from flightops.data.otp import load_otp
from flightops.features.otp_features import (
    OTPFeatureBuilder,
    add_derived_features,
    feature_importance_groups,
    route_hour_baseline,
    temporal_frames,
)
from flightops.io import read_json, save_joblib, write_json
from flightops.logging_utils import get_logger, log_section
from flightops.models.evaluation import (
    best_cost_threshold,
    calibration_curve_points,
    classification_metrics,
    threshold_cost_table,
)

logger = get_logger("models.otp")


@dataclass
class OTPArtifacts:
    builder: OTPFeatureBuilder
    booster: lgb.Booster
    calibrator: Any
    logistic_pipeline: Pipeline | None
    logistic_calibrator: Any | None
    threshold: float
    metrics: dict[str, Any]


def _lgb_params(cfg: Config) -> dict[str, Any]:
    model_cfg = cfg.get("otp.lgbm", {})
    return {
        "objective": "binary",
        "metric": "auc",
        "boosting_type": "gbdt",
        "learning_rate": float(model_cfg.get("learning_rate", 0.05)),
        "num_leaves": int(model_cfg.get("num_leaves", 127)),
        "min_data_in_leaf": int(model_cfg.get("min_child_samples", 150)),
        "feature_fraction": float(model_cfg.get("colsample_bytree", 0.85)),
        "bagging_fraction": float(model_cfg.get("subsample", 0.9)),
        "bagging_freq": int(model_cfg.get("subsample_freq", 1)),
        "lambda_l2": float(model_cfg.get("reg_lambda", 1.0)),
        "num_threads": int(model_cfg.get("n_jobs", -1)) if int(model_cfg.get("n_jobs", -1)) > 0 else 6,
        "verbosity": -1,
        "seed": int(cfg.get("project.seed", 42)),
        "feature_pre_filter": False,
    }


def _fit_calibrator(y_val: np.ndarray, p_val: np.ndarray, method: str) -> Any:
    if method == "none":
        return None
    if method == "sigmoid":
        model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        model.fit(np.log(np.clip(p_val, 1e-6, 1 - 1e-6) / np.clip(1 - p_val, 1e-6, 1)).reshape(-1, 1), y_val)
        return model
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(p_val, y_val)
    return calibrator


def _apply_calibrator(calibrator: Any, p: np.ndarray) -> np.ndarray:
    if calibrator is None:
        return p
    if isinstance(calibrator, IsotonicRegression):
        return np.clip(calibrator.predict(p), 1e-5, 1 - 1e-5)
    logits = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1)).reshape(-1, 1)
    return np.clip(calibrator.predict_proba(logits)[:, 1], 1e-5, 1 - 1e-5)


def _logistic_pipeline(builder: OTPFeatureBuilder, cfg: Config) -> Pipeline:
    numeric = builder.numeric_features
    categorical = builder.categorical_features
    preprocess = ColumnTransformer(
        transformers=[
            ("num", SimpleImputer(strategy="median"), numeric),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", min_frequency=100, sparse_output=True),
                categorical,
            ),
        ],
        remainder="drop",
    )
    return Pipeline(
        [
            ("prep", preprocess),
            (
                "model",
                LogisticRegression(
                    C=float(cfg.get("otp.logistic.C", 0.5)),
                    max_iter=int(cfg.get("otp.logistic.max_iter", 400)),
                    solver=str(cfg.get("otp.logistic.solver", "saga")),
                    random_state=int(cfg.get("project.seed", 42)),
                ),
            ),
        ]
    )


def _save_curve_figures(
    y_test: np.ndarray,
    probabilities: dict[str, np.ndarray],
    figures_dir: Path,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for name, p in probabilities.items():
        fpr, tpr, _ = roc_curve(y_test, p)
        precision, recall, _ = precision_recall_curve(y_test, p)
        axes[0].plot(fpr, tpr, label=name, linewidth=1.6)
        axes[1].plot(recall, precision, label=name, linewidth=1.6)
    axes[0].plot([0, 1], [0, 1], "k--", linewidth=0.8)
    axes[0].set_title("ROC curve - held-out Nov/Dec")
    axes[0].set_xlabel("False positive rate")
    axes[0].set_ylabel("True positive rate")
    axes[1].set_title("Precision-recall curve - held-out Nov/Dec")
    axes[1].set_xlabel("Recall")
    axes[1].set_ylabel("Precision")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures_dir / "otp_roc_pr.png", dpi=160)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(6.4, 4.2))
    for name, p in probabilities.items():
        prob_true, prob_pred = calibration_curve_points(y_test, p)
        axis.plot(prob_pred, prob_true, marker="o", markersize=3, label=name, linewidth=1.4)
    axis.plot([0, 1], [0, 1], "k--", linewidth=0.8, label="Perfect calibration")
    axis.set_xlabel("Mean predicted risk")
    axis.set_ylabel("Observed delay rate")
    axis.set_title("Calibration - held-out Nov/Dec")
    axis.grid(alpha=0.25)
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures_dir / "otp_calibration.png", dpi=160)
    plt.close(fig)


def _business_case(
    test: pd.DataFrame,
    p_test: np.ndarray,
    threshold: float,
    cfg: Config,
) -> dict[str, Any]:
    y = test["dep_del15"].astype(int).to_numpy()
    flagged = p_test >= threshold
    n_flagged = int(flagged.sum())
    tp = int(((y == 1) & flagged).sum())
    fp = int(((y == 0) & flagged).sum())
    avg_delay_min = float(test.loc[flagged & (y == 1), "dep_delay_min"].mean())
    cost_per_min = float(cfg.get("otp.business_case.delay_cost_usd_per_minute", 85))
    reaccom = float(cfg.get("otp.business_case.passenger_reaccommodation_usd", 1200))
    intervention = float(cfg.get("otp.business_case.intervention_cost_usd_per_flight", 250))
    prevention = float(cfg.get("otp.business_case.prevention_rate", 0.10))
    delay_cost_per_positive = avg_delay_min * cost_per_min + reaccom

    scenarios = []
    prevention_values = cfg.get("otp.business_case.prevention_scenarios", [0.05, 0.10, 0.20])
    for rate in [float(value) for value in prevention_values]:
        preventable = tp * rate
        gross_avoided = preventable * delay_cost_per_positive
        intervention_cost = n_flagged * intervention
        net = gross_avoided - intervention_cost
        scenarios.append(
            {
                "prevention_rate": round(rate, 3),
                "preventable_delays": round(preventable, 1),
                "gross_delay_cost_avoided_usd": round(gross_avoided, 0),
                "intervention_cost_usd": round(intervention_cost, 0),
                "net_benefit_usd": round(net, 0),
                "roi": round(net / max(intervention_cost, 1), 3),
            }
        )
    return {
        "threshold": round(float(threshold), 4),
        "flights_evaluated": int(len(y)),
        "flagged": n_flagged,
        "flag_rate": round(n_flagged / max(len(y), 1), 4),
        "true_positives": tp,
        "false_positives": fp,
        "precision": round(tp / max(n_flagged, 1), 4),
        "recall": round(tp / max(int(y.sum()), 1), 4),
        "avg_delay_minutes_among_flagged_positives": round(avg_delay_min, 1),
        "assumed_cost_per_delay_usd": round(delay_cost_per_positive, 0),
        "intervention_cost_per_flagged_flight_usd": intervention,
        "scenarios": scenarios,
        "note": "Causal prevention is an explicit planning assumption, not a measured treatment effect.",
    }


def train_otp_models(cfg: Config, force: bool = False) -> OTPArtifacts:
    """Train baseline, logistic and LightGBM models; persist all artifacts."""
    ensure_directories(cfg)
    set_global_seed()
    metrics_path = cfg.path("paths.metrics") / "otp_metrics.json"
    model_path = cfg.path("paths.models") / "otp_lightgbm.txt"
    if metrics_path.exists() and model_path.exists() and not force:
        logger.info("OTP artifacts already exist; set force=True to retrain")
        return load_otp_artifacts(cfg)

    log_section(logger, "OTP: load and split")
    frame = add_derived_features(load_otp(cfg))
    logger.info("Loaded %d flight legs", len(frame))
    train, val, test = temporal_frames(frame, cfg)
    logger.info("Rows train=%d val=%d test=%d", len(train), len(val), len(test))

    builder = OTPFeatureBuilder().fit(train)
    X_train = builder.transform(train)
    X_val = builder.transform(val)
    X_test = builder.transform(test)
    y_train = train["dep_del15"].astype(int).to_numpy()
    y_val = val["dep_del15"].astype(int).to_numpy()
    y_test = test["dep_del15"].astype(int).to_numpy()
    logger.info("Feature matrix: %s (%d features)", X_train.shape, len(builder.feature_columns))

    # Transparent non-ML baseline: route-hour rate fitted on training months.
    p_baseline_val = route_hour_baseline(train, val, min_count=int(cfg.get("data.otp.min_route_hour_flights", 30)))
    p_baseline_test = route_hour_baseline(train, test, min_count=int(cfg.get("data.otp.min_route_hour_flights", 30)))

    # Logistic regression on a documented subsample to keep the prototype light.
    logistic_cfg = cfg.get("otp.logistic", {})
    max_logistic_rows = int(logistic_cfg.get("max_rows", 750_000)) if isinstance(logistic_cfg, dict) else 750_000
    if len(train) > max_logistic_rows:
        logistic_train = train.sample(n=max_logistic_rows, random_state=int(cfg.get("project.seed", 42)))
        X_log_train = builder.transform(logistic_train)
        y_log_train = logistic_train["dep_del15"].astype(int).to_numpy()
        logger.info("Logistic baseline trained on %d-row random subsample", max_logistic_rows)
    else:
        X_log_train, y_log_train = X_train, y_train
    logistic = _logistic_pipeline(builder, cfg)
    logger.info("Fitting logistic baseline")
    logistic.fit(X_log_train, y_log_train)
    p_log_val_raw = logistic.predict_proba(X_val)[:, 1]
    p_log_test_raw = logistic.predict_proba(X_test)[:, 1]

    # Gradient-boosted model.
    log_section(logger, "OTP: LightGBM training")
    train_data = lgb.Dataset(
        X_train,
        label=y_train,
        categorical_feature=builder.categorical_features,
        free_raw_data=False,
    )
    val_data = lgb.Dataset(
        X_val,
        label=y_val,
        categorical_feature=builder.categorical_features,
        reference=train_data,
        free_raw_data=False,
    )
    callbacks = [
        lgb.early_stopping(stopping_rounds=50, first_metric_only=True, verbose=True),
        lgb.log_evaluation(period=100),
    ]
    booster = lgb.train(
        _lgb_params(cfg),
        train_data,
        num_boost_round=int(cfg.get("otp.lgbm.n_estimators", 600)),
        valid_sets=[val_data],
        valid_names=["validation"],
        callbacks=callbacks,
    )
    p_lgb_val_raw = booster.predict(X_val, num_iteration=booster.best_iteration)
    p_lgb_test_raw = booster.predict(X_test, num_iteration=booster.best_iteration)

    # Calibrate every probabilistic model on validation only.
    method = str(cfg.get("otp.calibration", "isotonic"))
    calibrators = {
        "route_hour_baseline": _fit_calibrator(y_val, p_baseline_val, "isotonic"),
        "logistic_regression": _fit_calibrator(y_val, p_log_val_raw, method),
        "lightgbm": _fit_calibrator(y_val, p_lgb_val_raw, method),
    }
    probabilities = {
        "route_hour_baseline": _apply_calibrator(calibrators["route_hour_baseline"], p_baseline_test),
        "logistic_regression": _apply_calibrator(calibrators["logistic_regression"], p_log_test_raw),
        "lightgbm": _apply_calibrator(calibrators["lightgbm"], p_lgb_test_raw),
    }

    # Cost-optimal threshold from validation predictions (not test).
    val_prob = _apply_calibrator(calibrators["lightgbm"], p_lgb_val_raw)
    cost_table_val = threshold_cost_table(
        y_val,
        val_prob,
        false_negative_usd=float(cfg.get("otp.threshold_costs.false_negative_usd", 4500)),
        false_positive_usd=float(cfg.get("otp.threshold_costs.false_positive_usd", 250)),
    )
    best = best_cost_threshold(cost_table_val)
    threshold = float(best["threshold"])
    top_decile_threshold = float(np.quantile(val_prob, 1 - float(cfg.get("otp.business_case.target_flag_rate", 0.10))))

    model_metrics: dict[str, Any] = {}
    for name, p in probabilities.items():
        model_metrics[name] = classification_metrics(y_test, p, threshold=0.5)
        model_metrics[name]["at_operating_threshold"] = classification_metrics(y_test, p, threshold=threshold)
        model_metrics[name]["at_top_decile_threshold"] = classification_metrics(
            y_test, p, threshold=top_decile_threshold
        )

    business_case = _business_case(test, probabilities["lightgbm"], top_decile_threshold, cfg)
    business_case_at_half = _business_case(test, probabilities["lightgbm"], 0.5, cfg)

    # SHAP on a reproducible sample of the held-out period.
    log_section(logger, "OTP: SHAP explainability")
    sample_size = min(int(cfg.get("otp.shap_sample_size", 6000)), len(X_test))
    sample = X_test.sample(n=sample_size, random_state=int(cfg.get("project.seed", 42)))
    explainer = shap.TreeExplainer(booster)
    shap_values = explainer.shap_values(sample)
    if isinstance(shap_values, list):
        shap_values = shap_values[-1]
    shap_values = np.asarray(shap_values)
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, -1]
    mean_abs = np.abs(shap_values).mean(axis=0)
    importance = pd.DataFrame(
        {
            "feature": builder.feature_columns,
            "mean_abs_shap": mean_abs,
            "group": [feature_importance_groups([f])[f] for f in builder.feature_columns],
        }
    ).sort_values("mean_abs_shap", ascending=False)
    importance.to_csv(cfg.path("paths.metrics") / "otp_shap_importance.csv", index=False)

    figures = cfg.path("paths.figures")
    rng = np.random.default_rng(int(cfg.get("project.seed", 42)))
    plt.figure()
    shap.summary_plot(shap_values, sample, plot_type="bar", max_display=18, show=False, rng=rng)
    plt.tight_layout()
    plt.savefig(figures / "otp_shap_bar.png", dpi=160, bbox_inches="tight")
    plt.close()
    plt.figure()
    shap.summary_plot(shap_values, sample, max_display=18, show=False, rng=rng)
    plt.tight_layout()
    plt.savefig(figures / "otp_shap_beeswarm.png", dpi=160, bbox_inches="tight")
    plt.close()

    _save_curve_figures(y_test, probabilities, figures)
    fig, axis = plt.subplots(figsize=(6.4, 4.0))
    axis.plot(cost_table_val["threshold"], cost_table_val["total_cost_usd"] / 1000.0)
    axis.axvline(threshold, color="tab:red", linestyle="--", label=f"Selected={threshold:.2f}")
    axis.set_xlabel("Probability threshold")
    axis.set_ylabel("Validation cost (USD thousands)")
    axis.set_title("Threshold selection uses validation only")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(figures / "otp_threshold_cost.png", dpi=160)
    plt.close(fig)

    # Persist artifacts.
    booster.save_model(str(model_path))
    save_joblib(logistic, cfg.path("paths.models") / "otp_logistic.joblib")
    for name, calibrator in calibrators.items():
        if calibrator is not None:
            save_joblib(calibrator, cfg.path("paths.models") / f"otp_calibrator_{name}.joblib")
    builder.to_json(cfg.path("paths.encoders") / "otp_feature_builder.json")

    test_meta = test[["flight_date", "carrier", "route", "origin", "dest", "dep_hour", "dep_delay_min", "dep_del15"]].copy()
    test_meta["risk_lightgbm"] = probabilities["lightgbm"]
    test_meta["risk_logistic"] = probabilities["logistic_regression"]
    test_meta["risk_baseline"] = probabilities["route_hour_baseline"]
    test_meta.to_parquet(cfg.path("paths.metrics") / "otp_test_predictions.parquet", index=False)

    metrics = {
        "data": {
            "source": cfg.get("data.otp.source"),
            "train_rows": int(len(train)),
            "val_rows": int(len(val)),
            "test_rows": int(len(test)),
            "train_period": f"{train['flight_date'].min().date()} to {train['flight_date'].max().date()}",
            "val_period": f"{val['flight_date'].min().date()} to {val['flight_date'].max().date()}",
            "test_period": f"{test['flight_date'].min().date()} to {test['flight_date'].max().date()}",
            "positive_rate_train": round(float(y_train.mean()), 4),
            "positive_rate_test": round(float(y_test.mean()), 4),
        },
        "features": {
            "n_features": len(builder.feature_columns),
            "numeric": builder.numeric_features,
            "categorical": builder.categorical_features,
        },
        "models": model_metrics,
        "operating_threshold": {
            "selected": round(threshold, 4),
            "selection": "minimum validation business cost",
            "top_decile_threshold": round(top_decile_threshold, 4),
            "recommended": round(top_decile_threshold, 4),
            "recommendation_basis": "top-decile targeting capacity; threshold 0.5 is available for a balanced default",
            "validation_cost_table": cost_table_val.to_dict(orient="records")[::5],
            "best_validation": best,
        },
        "business_case_top_decile": business_case,
        "business_case_at_0_5": business_case_at_half,
        "shap": {
            "sample_size": int(sample_size),
            "top_features": importance.head(20).to_dict(orient="records"),
            "group_importance": importance.groupby("group")["mean_abs_shap"].sum().sort_values(ascending=False).to_dict(),
        },
        "best_iteration": int(booster.best_iteration or 0),
        "notes": [
            "Test months are strictly after validation months.",
            "Isotonic calibrators were fitted on validation predictions only.",
            "Route-hour rates were fitted on training months only.",
            "Aircraft type is a documented synthetic enrichment (BTS has no equipment field).",
        ],
    }
    write_json(metrics, metrics_path)
    logger.info("OTP metrics written to %s", metrics_path)
    logger.info(
        "LightGBM test ROC-AUC=%.4f PR-AUC=%.4f Brier=%.4f",
        model_metrics["lightgbm"]["roc_auc"],
        model_metrics["lightgbm"]["pr_auc"],
        model_metrics["lightgbm"]["brier"],
    )
    # Serving uses the recommended top-decile threshold; the cost-optimal
    # threshold remains available in metrics for sensitivity analysis.
    return OTPArtifacts(
        builder,
        booster,
        calibrators["lightgbm"],
        logistic,
        calibrators["logistic_regression"],
        top_decile_threshold,
        metrics,
    )


def load_otp_artifacts(cfg: Config) -> OTPArtifacts:
    """Load persisted models and metrics for the app / batch scoring."""
    builder = OTPFeatureBuilder.from_json(cfg.path("paths.encoders") / "otp_feature_builder.json")
    booster = lgb.Booster(model_file=str(cfg.path("paths.models") / "otp_lightgbm.txt"))
    calibrator = None
    calibrator_path = cfg.path("paths.models") / "otp_calibrator_lightgbm.joblib"
    if calibrator_path.exists():
        calibrator = joblib.load(calibrator_path)
    logistic = None
    logistic_path = cfg.path("paths.models") / "otp_logistic.joblib"
    if logistic_path.exists():
        logistic = joblib.load(logistic_path)
    logistic_calibrator = None
    logistic_cal_path = cfg.path("paths.models") / "otp_calibrator_logistic_regression.joblib"
    if logistic_cal_path.exists():
        logistic_calibrator = joblib.load(logistic_cal_path)
    metrics = read_json(cfg.path("paths.metrics") / "otp_metrics.json")
    threshold = float(
        metrics.get("operating_threshold", {}).get("recommended")
        or metrics.get("operating_threshold", {}).get("selected", 0.5)
    )
    return OTPArtifacts(builder, booster, calibrator, logistic, logistic_calibrator, threshold, metrics)
