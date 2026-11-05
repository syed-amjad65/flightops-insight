"""Model evaluation utilities shared by OTP and RUL workstreams."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_recall_fscore_support,
    roc_auc_score,
)


def expected_calibration_error(
    y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10
) -> tuple[float, pd.DataFrame]:
    """Compute ECE and the calibration table (quantile bins)."""
    frame = pd.DataFrame({"y": y_true, "p": y_prob})
    frame["bin"] = pd.qcut(frame["p"].rank(method="first"), q=n_bins, labels=False, duplicates="drop")
    table = frame.groupby("bin").agg(
        n=("y", "size"), mean_predicted=("p", "mean"), observed_rate=("y", "mean")
    )
    table["gap"] = (table["mean_predicted"] - table["observed_rate"]).abs()
    ece = float((table["n"] / table["n"].sum() * table["gap"]).sum())
    return ece, table.reset_index(drop=True)


def classification_metrics(
    y_true: np.ndarray | pd.Series,
    y_prob: np.ndarray,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Full binary-classification scorecard used in model cards and the app."""
    y = np.asarray(y_true).astype(int)
    p = np.clip(np.asarray(y_prob, dtype=float), 1e-6, 1 - 1e-6)
    y_hat = (p >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y, y_hat, average="binary", zero_division=0
    )
    tn, fp, fn, tp = confusion_matrix(y, y_hat, labels=[0, 1]).ravel()
    ece, calibration_table = expected_calibration_error(y, p)

    order = np.argsort(-p)
    top_n = max(1, int(len(y) * 0.10))
    top_idx = order[:top_n]
    base_rate = float(y.mean())
    top_precision = float(y[top_idx].mean())
    capture = float(y[top_idx].sum() / max(1, y.sum()))

    return {
        "n": int(len(y)),
        "base_rate": round(base_rate, 4),
        "threshold": round(float(threshold), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
        "log_loss": round(float(log_loss(y, p)), 4),
        "ece": round(ece, 4),
        "tp": int(tp),
        "fp": int(fp),
        "tn": int(tn),
        "fn": int(fn),
        "top_decile_precision": round(top_precision, 4),
        "top_decile_lift": round(top_precision / max(base_rate, 1e-9), 2),
        "top_decile_capture": round(capture, 4),
        "calibration": calibration_table.to_dict(orient="records"),
    }


def threshold_cost_table(
    y_true: np.ndarray | pd.Series,
    y_prob: np.ndarray,
    false_negative_usd: float,
    false_positive_usd: float,
    thresholds: np.ndarray | None = None,
) -> pd.DataFrame:
    """Evaluate business cost across probability thresholds."""
    y = np.asarray(y_true).astype(int)
    p = np.asarray(y_prob, dtype=float)
    thresholds = thresholds if thresholds is not None else np.round(np.arange(0.05, 0.96, 0.01), 2)
    rows: list[dict[str, float]] = []
    for threshold in thresholds:
        y_hat = (p >= threshold).astype(int)
        tp = int(((y == 1) & (y_hat == 1)).sum())
        fp = int(((y == 0) & (y_hat == 1)).sum())
        fn = int(((y == 1) & (y_hat == 0)).sum())
        rows.append(
            {
                "threshold": float(threshold),
                "flagged": tp + fp,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": tp / max(tp + fp, 1),
                "recall": tp / max(tp + fn, 1),
                "total_cost_usd": fn * false_negative_usd + fp * false_positive_usd,
            }
        )
    return pd.DataFrame(rows)


def best_cost_threshold(table: pd.DataFrame) -> dict[str, float]:
    row = table.loc[table["total_cost_usd"].idxmin()]
    return {
        "threshold": float(row["threshold"]),
        "total_cost_usd": float(row["total_cost_usd"]),
        "precision": float(row["precision"]),
        "recall": float(row["recall"]),
        "flagged": int(row["flagged"]),
    }


def calibration_curve_points(
    y_true: np.ndarray | pd.Series, y_prob: np.ndarray, n_bins: int = 10
) -> tuple[np.ndarray, np.ndarray]:
    prob_true, prob_pred = calibration_curve(
        np.asarray(y_true).astype(int), np.asarray(y_prob), n_bins=n_bins, strategy="quantile"
    )
    return prob_true, prob_pred


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """RMSE/MAE/R2 plus the asymmetric NASA PHM score."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    error = p - y
    rmse = float(np.sqrt(np.mean(error**2)))
    mae = float(np.mean(np.abs(error)))
    ss_res = float(np.sum(error**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    # NASA/PHM08 asymmetric score: late predictions (p > y) cost more.
    score = np.where(error < 0, np.exp(-error / 13.0) - 1.0, np.exp(error / 10.0) - 1.0)
    return {
        "n": int(len(y)),
        "rmse": round(rmse, 3),
        "mae": round(mae, 3),
        "r2": round(float(r2), 4),
        "nasa_score_sum": round(float(score.sum()), 2),
        "nasa_score_mean": round(float(score.mean()), 3),
        "late_prediction_rate": round(float((error > 0).mean()), 4),
        "p90_abs_error": round(float(np.quantile(np.abs(error), 0.90)), 3),
    }
