"""Unit tests for OTP feature engineering and leakage guards."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from flightops.config import load_config
from flightops.features.otp_features import (
    OTPFeatureBuilder,
    add_derived_features,
    assign_time_split,
    model_eligible,
    route_hour_baseline,
    temporal_frames,
)


def _sample_frame(n: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    frame = pd.DataFrame(
        {
            "flight_date": dates,
            "month": dates.month.astype("int16"),
            "day_of_week": (dates.day_of_week + 1).astype("int8"),
            "day_of_year": dates.day_of_year.astype("int16"),
            "carrier": ["AA", "DL", "WN"] * (n // 3) + ["AA"] * (n % 3),
            "tail_number": [f"N{1000+i}" for i in range(n)],
            "flight_number": np.arange(100, 100 + n),
            "origin": ["DFW", "ATL", "ORD"] * (n // 3) + ["DFW"] * (n % 3),
            "dest": ["ATL", "ORD", "DFW"] * (n // 3) + ["ATL"] * (n % 3),
            "origin_state": ["TX", "GA", "IL"] * (n // 3) + ["TX"] * (n % 3),
            "dest_state": ["GA", "IL", "TX"] * (n // 3) + ["GA"] * (n % 3),
            "sched_dep_ts": dates + pd.Timedelta(hours=8),
            "sched_arr_ts": dates + pd.Timedelta(hours=11),
            "dep_hour": np.tile([6, 13, 19], n // 3 + 1)[:n],
            "dep_minute": np.zeros(n, dtype=int),
            "dep_delay_min": rng.normal(10, 25, n),
            "dep_del15": rng.integers(0, 2, n),
            "arr_delay_min": rng.normal(5, 20, n),
            "arr_del15": rng.integers(0, 2, n),
            "cancelled": 0,
            "diverted": 0,
            "distance_mi": rng.uniform(200, 1800, n),
            "crs_elapsed_min": rng.uniform(60, 240, n),
            "aircraft_type": ["A320", "B738", "E175"] * (n // 3) + ["A320"] * (n % 3),
            "aircraft_family": ["narrowbody", "narrowbody", "regional"] * (n // 3) + ["narrowbody"] * (n % 3),
            "seats": 160,
            "min_turn_min": 45,
            "sched_turn_min": np.tile([30.0, 55.0, np.nan], n // 3 + 1)[:n],
            "prev_dep_delay_min": rng.normal(0, 10, n),
            "prev_arr_delay_obs": rng.normal(0, 20, n),
            "inbound_known": rng.integers(0, 2, n),
            "prev_cancelled": 0,
            "origin_hour_departures": rng.integers(1, 30, n),
            "origin_daily_departures": rng.integers(20, 300, n),
            "route_daily_frequency": rng.integers(1, 10, n),
            "is_holiday": 0,
            "is_weekend": 0,
            "is_hub_origin": 1,
            "is_hub_dest": 0,
        }
    )
    frame["route"] = frame["origin"] + "_" + frame["dest"]
    return frame


def test_derived_features_are_consistent() -> None:
    frame = add_derived_features(_sample_frame())
    assert (frame["route"] == frame["origin"] + "_" + frame["dest"]).all()
    assert frame["dep_time_block"].isin(
        ["early_morning", "morning", "afternoon", "evening", "night"]
    ).all()
    expected = frame["sched_turn_min"] - frame["min_turn_min"]
    pd.testing.assert_series_equal(frame["turnaround_buffer_min"], expected, check_names=False)
    assert frame.loc[frame["sched_turn_min"].isna(), "has_turnaround"].eq(0).all()


def test_builder_maps_unseen_category_to_unknown() -> None:
    train = _sample_frame()
    builder = OTPFeatureBuilder().fit(train)
    unseen = _sample_frame(3)
    unseen.loc[unseen.index[0], "carrier"] = "ZZ"
    matrix = builder.transform(unseen)
    assert matrix.loc[unseen.index[0], "carrier"] == "__UNKNOWN__"
    assert "dep_del15" not in builder.feature_columns
    assert set(builder.feature_columns).isdisjoint({"dep_delay_min", "arr_delay_min", "late_aircraft_delay_min"})


def test_time_split_has_no_overlap() -> None:
    cfg = load_config()
    frame = assign_time_split(_sample_frame(365), cfg)
    groups = {name: set(group["month"].unique()) for name, group in frame.groupby("split")}
    assert groups["train"].isdisjoint(groups["val"])
    assert groups["train"].isdisjoint(groups["test"])
    assert groups["val"].isdisjoint(groups["test"])


def test_route_hour_baseline_falls_back_for_unseen_route() -> None:
    train = _sample_frame(900)
    train["dep_del15"] = np.random.default_rng(3).integers(0, 2, len(train))
    evaluation = _sample_frame(30)
    evaluation.loc[:, "route"] = "ZZZ_ZZZ"
    rates = route_hour_baseline(train, evaluation, min_count=1)
    assert len(rates) == len(evaluation)
    assert np.isfinite(rates).all()
    assert ((rates >= 0) & (rates <= 1)).all()
