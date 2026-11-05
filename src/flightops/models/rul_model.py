"""Remaining-useful-life models for C-MAPSS FD001.

Models
------
1. Ridge on the latest sensor snapshot - transparent classical baseline.
2. XGBoost on 30-cycle window summary statistics - strong tabular baseline.
3. LSTM over the raw 30-cycle multivariate window - deep sequence model.

The split is by engine unit: validation engines are never seen in training;
the official 100 test engines are untouched until final evaluation.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from sklearn.linear_model import Ridge
from torch import nn
from torch.utils.data import DataLoader, TensorDataset
from xgboost import XGBRegressor

from flightops.config import Config, ensure_directories, set_global_seed
from flightops.data.rul import load_rul
from flightops.features.rul_features import (
    RULScaler,
    build_sequence_dataset,
    select_informative_sensors,
    summary_feature_names,
    unit_train_val_split,
    window_summary_features,
)
from flightops.io import save_joblib, write_json
from flightops.logging_utils import get_logger, log_section
from flightops.models.evaluation import regression_metrics

logger = get_logger("models.rul")


class RULNet(nn.Module):
    """Small LSTM regressor with a regularised feed-forward head."""

    def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        return self.head(output[:, -1, :]).squeeze(-1)


@dataclass
class RULArtifacts:
    scaler: RULScaler
    features: list[str]
    window: int
    lstm: RULNet
    xgboost: XGBRegressor
    ridge: Ridge
    conformal_q: float
    metrics: dict[str, Any]


def _scale_sequences(sequences: np.ndarray, scaler: RULScaler) -> np.ndarray:
    mean = np.array([scaler.mean[f] for f in scaler.features], dtype=np.float32)
    std = np.array([scaler.std[f] for f in scaler.features], dtype=np.float32)
    return ((sequences - mean) / std).astype(np.float32)


def _train_lstm(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    cfg: Config,
) -> tuple[RULNet, list[dict[str, float]]]:
    params = cfg.get("rul.lstm", {})
    set_global_seed()
    torch.set_num_threads(6)
    model = RULNet(
        input_size=x_train.shape[2],
        hidden_size=int(params.get("hidden_size", 64)),
        num_layers=int(params.get("num_layers", 2)),
        dropout=float(params.get("dropout", 0.2)),
    )
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=float(params.get("learning_rate", 0.001)),
        weight_decay=float(params.get("weight_decay", 1e-5)),
    )
    loss_fn = nn.MSELoss()
    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
        batch_size=int(params.get("batch_size", 128)),
        shuffle=True,
        num_workers=0,
    )
    x_val_t = torch.from_numpy(x_val)
    y_val_t = torch.from_numpy(y_val)
    best_rmse = float("inf")
    best_state = copy.deepcopy(model.state_dict())
    patience_counter = 0
    history: list[dict[str, float]] = []

    for epoch in range(1, int(params.get("epochs", 80)) + 1):
        model.train()
        total_loss = 0.0
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            prediction = model(batch_x)
            loss = loss_fn(prediction, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.item()) * len(batch_x)
        model.eval()
        with torch.no_grad():
            val_pred = model(x_val_t).numpy()
        val_rmse = float(np.sqrt(np.mean((np.clip(val_pred, 0, None) - y_val) ** 2)))
        history.append(
            {"epoch": epoch, "train_mse": total_loss / max(len(x_train), 1), "val_rmse": val_rmse}
        )
        if val_rmse < best_rmse - 1e-4:
            best_rmse = val_rmse
            best_state = copy.deepcopy(model.state_dict())
            patience_counter = 0
        else:
            patience_counter += 1
        if epoch % 10 == 0 or epoch == 1:
            logger.info("LSTM epoch %d: val RMSE=%.2f (best=%.2f)", epoch, val_rmse, best_rmse)
        if patience_counter >= int(params.get("patience", 12)):
            logger.info("Early stopping at epoch %d", epoch)
            break
    model.load_state_dict(best_state)
    model.eval()
    return model, history


def _alert_cost_table(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    unscheduled_cost: float,
    early_cost: float,
    critical_cycles: int = 20,
    thresholds: np.ndarray | None = None,
) -> pd.DataFrame:
    """Cost of an RUL alert policy at each predicted-RUL threshold.

    ``y_true <= critical_cycles`` is the safety-relevant event (engine must be
    removed before it reaches the critical zone).  A predicted alert removes
    the engine early whether or not the event is true; a missed event is
    assumed to become an unscheduled removal.  Holding the event definition
    fixed makes thresholds comparable.
    """
    thresholds = thresholds if thresholds is not None else np.arange(5, 61, 1)
    rows = []
    true_alarm = y_true <= critical_cycles
    for threshold in thresholds:
        pred_alarm = y_pred <= threshold
        tp = int((pred_alarm & true_alarm).sum())
        fp = int((pred_alarm & ~true_alarm).sum())
        fn = int((~pred_alarm & true_alarm).sum())
        tn = int((~pred_alarm & ~true_alarm).sum())
        cost = (tp + fp) * early_cost + fn * unscheduled_cost
        rows.append(
            {
                "threshold_cycles": int(threshold),
                "critical_cycles": int(critical_cycles),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": tp / max(tp + fp, 1),
                "recall": tp / max(tp + fn, 1),
                "early_removals": tp + fp,
                "missed_unscheduled": fn,
                "total_cost_usd": cost,
            }
        )
    return pd.DataFrame(rows)


def train_rul_models(cfg: Config, force: bool = False) -> RULArtifacts:
    ensure_directories(cfg)
    set_global_seed()
    metrics_path = cfg.path("paths.metrics") / "rul_metrics.json"
    lstm_path = cfg.path("paths.models") / "rul_lstm.pt"
    if metrics_path.exists() and lstm_path.exists() and not force:
        logger.info("RUL artifacts already exist; set force=True to retrain")
        return load_rul_artifacts(cfg)

    log_section(logger, "RUL: load C-MAPSS")
    frame = load_rul(cfg)
    train_frame = frame[frame["split"] == "train"].copy()
    test_frame = frame[frame["split"] == "test"].copy()
    features = select_informative_sensors(train_frame)
    window = int(cfg.get("rul.window", 30))
    cap = float(cfg.get("rul.max_rul", 125))
    seed = int(cfg.get("project.seed", 42))
    train_units, val_units = unit_train_val_split(
        sorted(train_frame["unit"].unique().tolist()),
        float(cfg.get("rul.val_fraction", 0.2)),
        seed,
    )
    scaler = RULScaler().fit(train_frame, features)
    logger.info("Units train=%d val=%d test=%d; sensors=%d", len(train_units), len(val_units), test_frame["unit"].nunique(), len(features))

    x_train_raw, y_train, _, _ = build_sequence_dataset(
        train_frame, features, window, cap, train_units, stride=1
    )
    x_val_raw, y_val, _, _ = build_sequence_dataset(
        train_frame, features, window, cap, val_units, stride=1
    )
    x_test_raw, y_test, test_cycles, test_units = build_sequence_dataset(
        test_frame, features, window, 1e9, sorted(test_frame["unit"].unique().tolist()), last_window_only=True
    )
    x_train = _scale_sequences(x_train_raw, scaler)
    x_val = _scale_sequences(x_val_raw, scaler)
    x_test = _scale_sequences(x_test_raw, scaler)
    logger.info("Sequences train=%s val=%s test=%s", x_train.shape, x_val.shape, x_test.shape)

    # 1. Ridge on latest snapshot (classical, low-variance baseline).
    ridge = Ridge(alpha=1.0)
    ridge.fit(x_train[:, -1, :], y_train)
    pred_ridge = np.clip(ridge.predict(x_test[:, -1, :]), 0, cap)

    # 2. XGBoost on window summary statistics.
    log_section(logger, "RUL: XGBoost window baseline")
    xgb_cfg = cfg.get("rul.xgboost", {})
    xgb_train = window_summary_features(x_train, features)
    xgb_val = window_summary_features(x_val, features)
    xgb_test = window_summary_features(x_test, features)
    xgboost = XGBRegressor(
        n_estimators=int(xgb_cfg.get("n_estimators", 500)),
        max_depth=int(xgb_cfg.get("max_depth", 4)),
        learning_rate=float(xgb_cfg.get("learning_rate", 0.04)),
        subsample=float(xgb_cfg.get("subsample", 0.9)),
        colsample_bytree=float(xgb_cfg.get("colsample_bytree", 0.9)),
        reg_lambda=float(xgb_cfg.get("reg_lambda", 1.0)),
        objective="reg:squarederror",
        eval_metric="rmse",
        early_stopping_rounds=50,
        n_jobs=6,
        random_state=seed,
    )
    xgboost.fit(xgb_train, y_train, eval_set=[(xgb_val, y_val)], verbose=False)
    pred_xgb = np.clip(xgboost.predict(xgb_test), 0, cap)

    # 3. LSTM on raw scaled windows.
    log_section(logger, "RUL: LSTM training")
    lstm, history = _train_lstm(x_train, y_train, x_val, y_val, cfg)
    with torch.no_grad():
        pred_lstm = np.clip(lstm(torch.from_numpy(x_test)).numpy(), 0, cap)

    # Conformal interval from all validation windows (not just last window).
    with torch.no_grad():
        val_pred_all = np.clip(lstm(torch.from_numpy(x_val)).numpy(), 0, cap)
    alpha = float(cfg.get("rul.conformal_alpha", 0.10))
    conformal_q = float(np.quantile(np.abs(y_val - val_pred_all), 1 - alpha))
    test_coverage = float(np.mean(np.abs(y_test - pred_lstm) <= conformal_q))

    models = {
        "ridge_last_cycle": pred_ridge,
        "xgboost_window": pred_xgb,
        "lstm": pred_lstm,
    }
    metrics: dict[str, Any] = {
        "data": {
            "dataset": cfg.get("data.rul.subset", "FD001"),
            "train_units": len(train_units),
            "val_units": len(val_units),
            "test_units": int(test_frame["unit"].nunique()),
            "window_cycles": window,
            "rul_cap_cycles": cap,
            "sensors_used": features,
            "sensors_dropped_constant": [f"sensor_{i}" for i in range(1, 22) if f"sensor_{i}" not in features],
        },
        "models": {name: regression_metrics(y_test, pred) for name, pred in models.items()},
        "conformal": {
            "alpha": alpha,
            "q_cycles": round(conformal_q, 3),
            "test_coverage": round(test_coverage, 4),
            "interpretation": f"Prediction interval +/-{conformal_q:.1f} cycles achieved {test_coverage:.1%} coverage on test engines.",
        },
        "xgboost_best_iteration": int(getattr(xgboost, "best_iteration", 0) or 0),
    }

    # Alert / cost trade-off on the official test engines.
    unscheduled = float(cfg.get("rul.cost.unscheduled_removal_usd", 250_000))
    early = float(cfg.get("rul.cost.early_removal_usd", 75_000))
    cost_table = _alert_cost_table(
        y_test,
        pred_lstm,
        unscheduled,
        early,
        critical_cycles=int(cfg.get("rul.alert_threshold_cycles", 20)),
    )
    selected_threshold = int(cfg.get("rul.alert_threshold_cycles", 20))
    optimal = cost_table.loc[cost_table["total_cost_usd"].idxmin()]
    selected_row = cost_table[cost_table["threshold_cycles"] == selected_threshold].iloc[0]
    no_alert_cost = int((y_test <= selected_threshold).sum()) * unscheduled
    remove_all_cost = int(len(y_test)) * early
    metrics["alerts"] = {
        "selected_threshold_cycles": selected_threshold,
        "selected": {
            "tp": int(selected_row["tp"]),
            "fp": int(selected_row["fp"]),
            "fn": int(selected_row["fn"]),
            "tn": int(selected_row["tn"]),
            "precision": round(float(selected_row["precision"]), 4),
            "recall": round(float(selected_row["recall"]), 4),
            "total_cost_usd": int(selected_row["total_cost_usd"]),
        },
        "cost_optimal_threshold_cycles": int(optimal["threshold_cycles"]),
        "cost_optimal": {
            "tp": int(optimal["tp"]),
            "fp": int(optimal["fp"]),
            "fn": int(optimal["fn"]),
            "tn": int(optimal["tn"]),
            "precision": round(float(optimal["precision"]), 4),
            "recall": round(float(optimal["recall"]), 4),
            "total_cost_usd": int(optimal["total_cost_usd"]),
        },
        "cost_assumptions": {"unscheduled_removal_usd": unscheduled, "early_removal_usd": early},
        "benchmark_no_alert_cost_usd": no_alert_cost,
        "benchmark_remove_all_early_cost_usd": remove_all_cost,
        "savings_vs_no_alert_usd": int(no_alert_cost - selected_row["total_cost_usd"]),
        "savings_vs_remove_all_usd": int(remove_all_cost - selected_row["total_cost_usd"]),
        "note": "Costs are illustrative planning assumptions; C-MAPSS is simulated degradation.",
    }
    cost_table.to_csv(cfg.path("paths.metrics") / "rul_alert_cost.csv", index=False)

    # Sensitivity: +/-50% on both cost figures shifts the optimal threshold.
    sensitivity = []
    for factor in cfg.get("rul.cost.sensitivity_pct", [0.5, 0.75, 1.0, 1.25, 1.5]):
        table = _alert_cost_table(
            y_test,
            pred_lstm,
            unscheduled * factor,
            early * factor,
            critical_cycles=int(cfg.get("rul.alert_threshold_cycles", 20)),
        )
        row = table.loc[table["total_cost_usd"].idxmin()]
        sensitivity.append(
            {
                "cost_factor": factor,
                "optimal_threshold_cycles": int(row["threshold_cycles"]),
                "total_cost_usd": int(row["total_cost_usd"]),
            }
        )
    metrics["alert_sensitivity"] = sensitivity

    # Watchlist for the app and CAMO review.
    watchlist = pd.DataFrame(
        {
            "unit": test_units.astype(int),
            "last_cycle": test_cycles.astype(int),
            "predicted_rul_cycles": np.round(pred_lstm, 1),
            "true_rul_cycles": np.round(y_test, 1),
            "abs_error_cycles": np.round(np.abs(pred_lstm - y_test), 1),
            "prediction_low": np.round(np.clip(pred_lstm - conformal_q, 0, None), 1),
            "prediction_high": np.round(pred_lstm + conformal_q, 1),
        }
    )
    critical = float(cfg.get("rul.critical_threshold_cycles", 10))
    watch = float(cfg.get("rul.watch_threshold_cycles", 40))
    watchlist["risk_tier"] = np.select(
        [
            watchlist["predicted_rul_cycles"] <= critical,
            watchlist["predicted_rul_cycles"] <= selected_threshold,
            watchlist["predicted_rul_cycles"] <= watch,
        ],
        ["Critical - remove now", "High - schedule shop visit", "Watch - plan next visit"],
        default="Serviceable",
    )
    watchlist["recommended_action"] = np.select(
        [
            watchlist["predicted_rul_cycles"] <= critical,
            watchlist["predicted_rul_cycles"] <= selected_threshold,
            watchlist["predicted_rul_cycles"] <= watch,
        ],
        [
            "Ground aircraft / remove engine; complete engineering review.",
            "Reserve a shop slot within 7 days; increase borescope monitoring.",
            "Plan next shop visit; re-score weekly.",
        ],
        default="Continue service; re-score at next check.",
    )
    watchlist.to_csv(cfg.path("paths.metrics") / "rul_watchlist.csv", index=False)
    metrics["watchlist_counts"] = watchlist["risk_tier"].value_counts().to_dict()

    # Figures.
    figures = cfg.path("paths.figures")
    fig, axis = plt.subplots(figsize=(5.6, 4.6))
    axis.scatter(y_test, pred_lstm, s=18, alpha=0.7, label="LSTM")
    axis.plot([0, cap], [0, cap], "k--", linewidth=1, label="Perfect")
    axis.set_xlabel("True RUL (cycles)")
    axis.set_ylabel("Predicted RUL (cycles)")
    axis.set_title("RUL: held-out test engines")
    axis.legend()
    axis.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "rul_predicted_vs_true.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0))
    errors = pred_lstm - y_test
    axes[0].hist(errors, bins=20, color="tab:blue", alpha=0.8)
    axes[0].axvline(0, color="black", linestyle="--", linewidth=1)
    axes[0].set_title("LSTM prediction error (predicted - true)")
    axes[0].set_xlabel("Cycles")
    axes[0].set_ylabel("Engines")
    axes[1].plot(cost_table["threshold_cycles"], cost_table["total_cost_usd"] / 1000)
    axes[1].axvline(selected_threshold, color="tab:red", linestyle="--", label=f"Selected={selected_threshold}")
    axes[1].axvline(optimal["threshold_cycles"], color="tab:green", linestyle=":", label=f"Cost-optimal={int(optimal['threshold_cycles'])}")
    axes[1].set_title("Alert threshold cost trade-off")
    axes[1].set_xlabel("RUL alert threshold (cycles)")
    axes[1].set_ylabel("Expected cost (USD thousands)")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "rul_alert_cost.png", dpi=160)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(6.4, 4.0))
    history_df = pd.DataFrame(history)
    axis.plot(history_df["epoch"], np.sqrt(history_df["train_mse"]), label="Train RMSE")
    axis.plot(history_df["epoch"], history_df["val_rmse"], label="Validation RMSE")
    axis.set_xlabel("Epoch")
    axis.set_ylabel("RMSE (cycles)")
    axis.set_title("LSTM training history")
    axis.legend()
    axis.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "rul_training_history.png", dpi=160)
    plt.close(fig)

    # Trajectory plot for a few test engines (all windows, not just last).
    sample_units = sorted(test_frame["unit"].unique())[:4]
    fig, axis = plt.subplots(figsize=(7.0, 4.2))
    for unit in sample_units:
        unit_frame = test_frame[test_frame["unit"] == unit]
        x_unit, y_unit, cycles_unit, _ = build_sequence_dataset(
            unit_frame, features, window, 1e9, [unit], last_window_only=False
        )
        if len(x_unit) == 0:
            continue
        with torch.no_grad():
            p_unit = np.clip(lstm(torch.from_numpy(_scale_sequences(x_unit, scaler))).numpy(), 0, cap)
        axis.plot(cycles_unit, y_unit, label=f"Unit {unit} true", linewidth=1.1)
        axis.plot(cycles_unit, p_unit, linestyle="--", label=f"Unit {unit} pred", linewidth=1.1)
    axis.set_xlabel("Flight cycle")
    axis.set_ylabel("RUL (cycles)")
    axis.set_title("RUL trajectories: representative test engines")
    axis.legend(fontsize=7, ncol=2)
    axis.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures / "rul_trajectories.png", dpi=160)
    plt.close(fig)

    # Persist.
    torch.save(lstm.state_dict(), lstm_path)
    torch.save(
        {
            "input_size": len(features),
            "hidden_size": int(cfg.get("rul.lstm.hidden_size", 64)),
            "num_layers": int(cfg.get("rul.lstm.num_layers", 2)),
            "dropout": float(cfg.get("rul.lstm.dropout", 0.2)),
        },
        cfg.path("paths.models") / "rul_lstm_config.pt",
    )
    save_joblib(xgboost, cfg.path("paths.models") / "rul_xgboost.joblib")
    save_joblib(ridge, cfg.path("paths.models") / "rul_ridge.joblib")
    scaler.to_json(cfg.path("paths.encoders") / "rul_scaler.json")
    (cfg.path("paths.encoders") / "rul_features.json").write_text(
        json.dumps({"features": features, "window": window, "cap": cap}, indent=2), encoding="utf-8"
    )
    write_json(metrics, metrics_path)
    logger.info("RUL metrics written to %s", metrics_path)
    logger.info(
        "LSTM RMSE=%.2f MAE=%.2f score=%.1f",
        metrics["models"]["lstm"]["rmse"],
        metrics["models"]["lstm"]["mae"],
        metrics["models"]["lstm"]["nasa_score_sum"],
    )
    return RULArtifacts(scaler, features, window, lstm, xgboost, ridge, conformal_q, metrics)


def load_rul_artifacts(cfg: Config) -> RULArtifacts:
    scaler = RULScaler.from_json(cfg.path("paths.encoders") / "rul_scaler.json")
    feature_payload = json.loads((cfg.path("paths.encoders") / "rul_features.json").read_text(encoding="utf-8"))
    config_payload = torch.load(cfg.path("paths.models") / "rul_lstm_config.pt", map_location="cpu", weights_only=True)
    model = RULNet(
        input_size=int(config_payload["input_size"]),
        hidden_size=int(config_payload["hidden_size"]),
        num_layers=int(config_payload["num_layers"]),
        dropout=float(config_payload["dropout"]),
    )
    model.load_state_dict(torch.load(cfg.path("paths.models") / "rul_lstm.pt", map_location="cpu", weights_only=True))
    model.eval()
    xgboost = joblib.load(cfg.path("paths.models") / "rul_xgboost.joblib")
    ridge = joblib.load(cfg.path("paths.models") / "rul_ridge.joblib")
    metrics = json.loads((cfg.path("paths.metrics") / "rul_metrics.json").read_text(encoding="utf-8"))
    return RULArtifacts(
        scaler=scaler,
        features=feature_payload["features"],
        window=int(feature_payload["window"]),
        lstm=model,
        xgboost=xgboost,
        ridge=ridge,
        conformal_q=float(metrics["conformal"]["q_cycles"]),
        metrics=metrics,
    )
