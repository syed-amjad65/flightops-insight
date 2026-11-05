"""Data-contract and reference-data tests."""

from __future__ import annotations

import pandas as pd
import pytest

from flightops.config import load_config
from flightops.data.aircraft import assign_aircraft_type, type_attributes
from flightops.data.contracts import validate_rul
from flightops.data.otp import SyntheticOTPProvider
from flightops.data.rul import SyntheticRULProvider


def test_aircraft_mapping_is_deterministic_and_complete() -> None:
    first = assign_aircraft_type("N12345", "AA")
    second = assign_aircraft_type("N12345", "AA")
    assert first == second
    family, seats, min_turn = type_attributes(first)
    assert family in {"narrowbody", "widebody", "regional"}
    assert seats > 0 and min_turn > 0


def test_synthetic_otp_matches_canonical_contract() -> None:
    cfg = load_config().with_overrides({"data": {"synthetic": {"n_flights": 3000}}})
    frame = SyntheticOTPProvider().load(cfg)
    assert len(frame) == 3000
    assert frame["dep_del15"].isin([0, 1]).all()
    assert frame["route"].notna().all()


def test_synthetic_rul_matches_contract_and_is_sorted() -> None:
    cfg = load_config()
    frame = SyntheticRULProvider().load(cfg)
    validate_rul(frame)
    assert frame.groupby(["split", "unit"])["cycle"].apply(lambda s: s.is_monotonic_increasing).all()
    assert (frame["rul"] >= 0).all()
