"""Data provider protocols.

Downstream modules depend on these protocols, not on BTS or C-MAPSS.  A real
Skywise export (or an internal lakehouse table) is integrated by implementing
``OTPDataProvider.load`` and returning the canonical schema in
``flightops.data.contracts``.  No proprietary access is claimed anywhere in
this repository.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from flightops.config import Config


class OTPDataProvider(Protocol):
    """Supplies flight-leg records in the canonical OTP schema."""

    name: str

    def load(self, cfg: Config) -> pd.DataFrame:
        ...


class RULDataProvider(Protocol):
    """Supplies run-to-failure turbofan trajectories in the canonical RUL schema."""

    name: str

    def load(self, cfg: Config) -> pd.DataFrame:
        ...


class SkywiseOTPAdapter:
    """Documented integration seam; deliberately not implemented.

    Expected Skywise/enterprise inputs
    ----------------------------------
    - flight leg: flight_date, carrier, tail, origin, dest, scheduled/actual
      timestamps, delay codes, cancellation flag
    - aircraft registry: tail -> type, family, seats, min turnaround
    - maintenance: tail -> check due dates, engine RUL

    Mapping to ``OTP_REQUIRED_COLUMNS`` happens in a thin adapter; feature,
    model, optimization and app code remain unchanged.
    """

    name = "skywise_adapter_not_configured"

    def load(self, cfg: Config) -> pd.DataFrame:  # pragma: no cover - integration stub
        raise NotImplementedError(
            "No Skywise credentials or export are configured. Use the BTS provider "
            "or the synthetic provider, or implement this adapter against an approved "
            "enterprise data contract."
        )
