"""Canonical dataset contracts.

The contracts are intentionally plain dataclasses (no pandera dependency) so
they can run in a minimal environment and be imported by tests.  Every data
provider must emit these columns before downstream feature code is allowed to
run.  This is the seam that lets a Skywise export replace the BTS extract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

OTP_REQUIRED_COLUMNS: tuple[str, ...] = (
    "flight_date",
    "month",
    "day_of_week",
    "day_of_year",
    "carrier",
    "tail_number",
    "flight_number",
    "origin",
    "dest",
    "origin_state",
    "dest_state",
    "sched_dep_ts",
    "sched_arr_ts",
    "dep_hour",
    "dep_minute",
    "dep_delay_min",
    "dep_del15",
    "arr_delay_min",
    "arr_del15",
    "cancelled",
    "diverted",
    "distance_mi",
    "crs_elapsed_min",
    "aircraft_type",
    "aircraft_family",
    "seats",
    "min_turn_min",
    "sched_turn_min",
    "prev_arr_delay_obs",
    "inbound_known",
    "origin_hour_departures",
    "origin_daily_departures",
    "route_daily_frequency",
)

RUL_REQUIRED_COLUMNS: tuple[str, ...] = (
    "unit",
    "cycle",
    "split",
    "rul",
    "op_setting_1",
    "op_setting_2",
    "op_setting_3",
    "sensor_1",
    "sensor_2",
    "sensor_3",
    "sensor_4",
    "sensor_5",
    "sensor_6",
    "sensor_7",
    "sensor_8",
    "sensor_9",
    "sensor_10",
    "sensor_11",
    "sensor_12",
    "sensor_13",
    "sensor_14",
    "sensor_15",
    "sensor_16",
    "sensor_17",
    "sensor_18",
    "sensor_19",
    "sensor_20",
    "sensor_21",
)


@dataclass(frozen=True)
class DatasetContract:
    name: str
    required_columns: tuple[str, ...]
    dtypes: Mapping[str, str]

    def validate(self, frame: pd.DataFrame, *, allow_empty: bool = False) -> None:
        missing = [column for column in self.required_columns if column not in frame.columns]
        if missing:
            raise ValueError(f"{self.name}: missing required columns: {missing}")
        if frame.empty and not allow_empty:
            raise ValueError(f"{self.name}: frame is empty")
        if frame.empty:
            return
        if "flight_date" in frame.columns and frame["flight_date"].isna().any():
            raise ValueError(f"{self.name}: flight_date contains nulls")
        if "dep_del15" in frame.columns:
            invalid = set(frame["dep_del15"].dropna().unique()) - {0, 1, True, False}
            if invalid:
                raise ValueError(f"{self.name}: dep_del15 must be binary, found {invalid}")
        if "cancelled" in frame.columns:
            if not frame["cancelled"].isin([0, 1, True, False]).all():
                raise ValueError(f"{self.name}: cancelled must be binary")
        if "unit" in frame.columns and frame["unit"].isna().any():
            raise ValueError(f"{self.name}: unit contains nulls")
        if "cycle" in frame.columns and (frame["cycle"].dropna() < 1).any():
            raise ValueError(f"{self.name}: cycle must be >= 1")


OTP_CONTRACT = DatasetContract(
    name="OTP canonical flights",
    required_columns=OTP_REQUIRED_COLUMNS,
    dtypes={
        "carrier": "string",
        "origin": "string",
        "dest": "string",
        "aircraft_type": "string",
    },
)

RUL_CONTRACT = DatasetContract(
    name="C-MAPSS canonical RUL",
    required_columns=RUL_REQUIRED_COLUMNS,
    dtypes={"unit": "int64", "cycle": "int64", "split": "string", "rul": "float64"},
)


def validate_otp(frame: pd.DataFrame) -> None:
    OTP_CONTRACT.validate(frame)


def validate_rul(frame: pd.DataFrame) -> None:
    RUL_CONTRACT.validate(frame)
