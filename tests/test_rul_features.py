"""Tests for RUL windowing and scaling."""

from __future__ import annotations

import numpy as np
import pandas as pd

from flightops.features.rul_features import (
    RULScaler,
    build_sequence_dataset,
    unit_train_val_split,
    window_summary_features,
)


def _unit_frame(unit: int, length: int = 60) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "unit": unit,
            "cycle": np.arange(1, length + 1),
            "rul": np.arange(length - 1, -1, -1, dtype=float),
            "sensor_2": np.linspace(1, 2, length),
            "sensor_3": np.linspace(5, 9, length),
            "sensor_4": np.linspace(10, 2, length),
        }
    )


def test_windows_have_correct_shape_and_target_alignment() -> None:
    frame = _unit_frame(1)
    features = ["sensor_2", "sensor_3", "sensor_4"]
    x, y, cycles, units = build_sequence_dataset(frame, features, 10, 125, [1])
    assert x.shape == (51, 10, 3)
    assert len(y) == len(cycles) == 51
    assert y[0] == 50
    assert cycles[0] == 10


def test_last_window_only_returns_one_sequence() -> None:
    frame = _unit_frame(1)
    x, y, cycles, units = build_sequence_dataset(frame, ["sensor_2", "sensor_3", "sensor_4"], 10, 1e9, [1], last_window_only=True)
    assert x.shape == (1, 10, 3)
    assert cycles[0] == 60
    assert y[0] == 0


def test_scaler_is_fitted_on_train_statistics() -> None:
    frame = _unit_frame(1).assign(sensor_2=lambda d: d["sensor_2"] * 10)
    scaler = RULScaler().fit(frame, ["sensor_2"])
    transformed = scaler.transform(frame)
    assert abs(float(transformed.mean())) < 1e-5


def test_unit_split_is_disjoint() -> None:
    train, val = unit_train_val_split(list(range(1, 21)), 0.2, 42)
    assert not set(train) & set(val)
    assert len(val) == 4
