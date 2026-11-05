"""Feature engineering for the OTP delay-risk model.

Design principles
-----------------
* Time-based splits only (month boundaries from config).
* No post-flight information is used as a feature.  Inbound actual delay is
  used only when the inbound aircraft had already arrived ``prediction_cutoff``
  minutes before the scheduled departure, which is encoded in
  ``inbound_known``.
* Historical route-hour rates are used only by the transparent baseline, and
  are fitted on the training months only.
* The fitted builder is serialisable so the Streamlit predictor and batch jobs
  construct exactly the same feature matrix as training.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from flightops.config import Config
from flightops.logging_utils import get_logger

logger = get_logger("features.otp")

NUMERIC_FEATURES: list[str] = [
    "month",
    "day_of_week",
    "day_of_year",
    "dep_hour",
    "dep_minute",
    "is_weekend",
    "is_holiday",
    "distance_mi",
    "crs_elapsed_min",
    "seats",
    "min_turn_min",
    "sched_turn_min",
    "turnaround_buffer_min",
    "prev_dep_delay_min",
    "prev_arr_delay_obs",
    "inbound_known",
    "has_turnaround",
    "prev_cancelled",
    "origin_hour_departures",
    "origin_daily_departures",
    "route_daily_frequency",
    "is_hub_origin",
    "is_hub_dest",
    "is_peak",
    "hour_sin",
    "hour_cos",
    "doy_sin",
    "doy_cos",
]

CATEGORICAL_FEATURES: list[str] = [
    "carrier",
    "origin",
    "dest",
    "route",
    "aircraft_type",
    "aircraft_family",
    "origin_state",
    "dest_state",
    "dep_time_block",
]

TARGET = "dep_del15"


def _time_block(hour: pd.Series) -> pd.Series:
    """Map departure hour to an interpretable operating block."""
    conditions = [
        hour.between(5, 7),
        hour.between(8, 11),
        hour.between(12, 16),
        hour.between(17, 20),
    ]
    choices = ["early_morning", "morning", "afternoon", "evening"]
    return pd.Series(
        np.select(conditions, choices, default="night"),
        index=hour.index,
        dtype="object",
    )


def add_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic, leakage-safe operational features."""
    df = frame.copy()
    df["route"] = df["origin"].astype("string") + "_" + df["dest"].astype("string")
    df["dep_time_block"] = _time_block(df["dep_hour"].astype("int16"))
    df["is_peak"] = df["dep_hour"].between(15, 20).astype("int8")
    df["sched_turn_min"] = pd.to_numeric(df["sched_turn_min"], errors="coerce")
    df["min_turn_min"] = pd.to_numeric(df["min_turn_min"], errors="coerce")
    df["turnaround_buffer_min"] = df["sched_turn_min"] - df["min_turn_min"]
    df["has_turnaround"] = df["sched_turn_min"].notna().astype("int8")
    df["prev_arr_delay_obs"] = pd.to_numeric(df["prev_arr_delay_obs"], errors="coerce").clip(-60, 360)
    df["prev_dep_delay_min"] = pd.to_numeric(df["prev_dep_delay_min"], errors="coerce").clip(-30, 360)
    df["hour_sin"] = np.sin(2 * np.pi * df["dep_hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["dep_hour"] / 24.0)
    df["doy_sin"] = np.sin(2 * np.pi * df["day_of_year"] / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["day_of_year"] / 365.25)
    return df


def assign_time_split(frame: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Assign train/validation/test by month, never randomly."""
    df = frame.copy()
    train_months = set(cfg.get("split.train_months", []))
    val_months = set(cfg.get("split.val_months", []))
    test_months = set(cfg.get("split.test_months", []))
    month = df["month"].astype("int16")
    df["split"] = np.select(
        [month.isin(train_months), month.isin(val_months), month.isin(test_months)],
        ["train", "val", "test"],
        default="unused",
    )
    return df


def model_eligible(frame: pd.DataFrame) -> pd.Series:
    """Completed flights with a valid binary target are model-eligible."""
    return (
        frame["cancelled"].eq(0)
        & frame["diverted"].eq(0)
        & frame["dep_del15"].notna()
        & frame["dep_delay_min"].notna()
    )


@dataclass
class OTPFeatureBuilder:
    """Fitted, serialisable feature contract shared by train and serving."""

    numeric_features: list[str] = field(default_factory=lambda: list(NUMERIC_FEATURES))
    categorical_features: list[str] = field(default_factory=lambda: list(CATEGORICAL_FEATURES))
    categories: dict[str, list[str]] = field(default_factory=dict)
    fitted: bool = False
    unknown_token: str = "__UNKNOWN__"

    @property
    def feature_columns(self) -> list[str]:
        return self.numeric_features + self.categorical_features

    def fit(self, frame: pd.DataFrame) -> "OTPFeatureBuilder":
        prepared = add_derived_features(frame)
        self.categories = {}
        for column in self.categorical_features:
            values = prepared[column].astype("string").fillna(self.unknown_token)
            levels = sorted(set(values.unique().tolist()) | {self.unknown_token})
            self.categories[column] = levels
        self.fitted = True
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not self.fitted:
            raise RuntimeError("OTPFeatureBuilder must be fitted before transform")
        df = add_derived_features(frame)
        output = pd.DataFrame(index=df.index)
        for column in self.numeric_features:
            if column not in df.columns:
                output[column] = np.nan
            else:
                output[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")
        for column in self.categorical_features:
            if column not in df.columns:
                values = pd.Series(self.unknown_token, index=df.index, dtype="string")
            else:
                values = df[column].astype("string").fillna(self.unknown_token)
            values = values.where(values.isin(self.categories[column]), self.unknown_token)
            output[column] = pd.Categorical(values, categories=self.categories[column])
        return output[self.feature_columns]

    def to_json(self, path: str | Path) -> Path:
        payload = {
            "numeric_features": self.numeric_features,
            "categorical_features": self.categorical_features,
            "categories": self.categories,
            "unknown_token": self.unknown_token,
        }
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination

    @classmethod
    def from_json(cls, path: str | Path) -> "OTPFeatureBuilder":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        builder = cls(
            numeric_features=payload["numeric_features"],
            categorical_features=payload["categorical_features"],
            categories=payload["categories"],
            unknown_token=payload.get("unknown_token", "__UNKNOWN__"),
        )
        builder.fitted = True
        return builder


def temporal_frames(
    frame: pd.DataFrame, cfg: Config
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return eligible train/validation/test frames in chronological order."""
    split = assign_time_split(frame, cfg)
    eligible = model_eligible(split)
    train = split[(split["split"] == "train") & eligible].copy()
    val = split[(split["split"] == "val") & eligible].copy()
    test = split[(split["split"] == "test") & eligible].copy()
    if train.empty or val.empty or test.empty:
        raise ValueError(
            f"Empty split: train={len(train)}, val={len(val)}, test={len(test)}. "
            "Check split months and data coverage."
        )
    return train, val, test


def route_hour_baseline(
    train: pd.DataFrame,
    evaluation: pd.DataFrame,
    min_count: int = 30,
    smoothing: float = 25.0,
) -> np.ndarray:
    """Transparent baseline: smoothed historical delay rate by route and hour.

    Fitted on train only. Falls back from route-hour to route to global rate,
    which mirrors a simple operational rule used by many OCC teams.
    """
    global_rate = float(train[TARGET].mean())
    route_hour = (
        train.groupby(["route", "dep_hour"], observed=True)[TARGET]
        .agg(["sum", "count"])
        .reset_index()
    )
    route = train.groupby("route", observed=True)[TARGET].agg(["sum", "count"]).reset_index()

    route_hour["rate"] = (route_hour["sum"] + smoothing * global_rate) / (
        route_hour["count"] + smoothing
    )
    route_hour.loc[route_hour["count"] < min_count, "rate"] = np.nan
    route["rate"] = (route["sum"] + smoothing * global_rate) / (route["count"] + smoothing)

    scored = evaluation[["route", "dep_hour"]].merge(route_hour, on=["route", "dep_hour"], how="left")
    scored = scored.merge(route[["route", "rate"]], on="route", how="left", suffixes=("", "_route"))
    rate = scored["rate"].fillna(scored["rate_route"]).fillna(global_rate).to_numpy()
    return np.clip(rate, 0.001, 0.999)


def save_feature_metadata(builder: OTPFeatureBuilder, cfg: Config) -> Path:
    return builder.to_json(cfg.path("paths.encoders") / "otp_feature_builder.json")


def feature_importance_groups(feature_names: Iterable[str]) -> dict[str, str]:
    """Map raw feature names to executive-friendly driver groups."""
    groups: dict[str, str] = {}
    for feature in feature_names:
        if feature in {
            "sched_turn_min",
            "turnaround_buffer_min",
            "min_turn_min",
            "prev_dep_delay_min",
            "prev_arr_delay_obs",
            "inbound_known",
            "has_turnaround",
            "prev_cancelled",
        }:
            groups[feature] = "Turnaround & inbound aircraft"
        elif feature in {
            "dep_hour",
            "dep_minute",
            "dep_time_block",
            "is_peak",
            "hour_sin",
            "hour_cos",
        }:
            groups[feature] = "Time of day"
        elif feature in {"month", "day_of_week", "day_of_year", "is_weekend", "is_holiday", "doy_sin", "doy_cos"}:
            groups[feature] = "Season & calendar"
        elif feature in {
            "origin",
            "dest",
            "route",
            "origin_state",
            "dest_state",
            "origin_hour_departures",
            "origin_daily_departures",
            "route_daily_frequency",
            "is_hub_origin",
            "is_hub_dest",
        }:
            groups[feature] = "Airport & network"
        elif feature in {"carrier"}:
            groups[feature] = "Carrier"
        elif feature in {"aircraft_type", "aircraft_family", "seats", "distance_mi", "crs_elapsed_min"}:
            groups[feature] = "Aircraft & route"
        else:
            groups[feature] = "Other"
    return groups
