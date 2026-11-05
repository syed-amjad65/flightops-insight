"""Serving helpers for the OTP model (shared by Streamlit and batch jobs)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd
import shap

from flightops.config import Config
from flightops.models.otp_model import OTPArtifacts, _apply_calibrator, load_otp_artifacts
from flightops.models.otp_model import logger


def risk_tier(probability: float) -> str:
    if probability >= 0.75:
        return "Critical"
    if probability >= 0.50:
        return "High"
    if probability >= 0.25:
        return "Moderate"
    return "Low"


@dataclass
class OTPPredictor:
    artifacts: OTPArtifacts

    @cached_property
    def explainer(self) -> shap.TreeExplainer:
        return shap.TreeExplainer(self.artifacts.booster)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        matrix = self.artifacts.builder.transform(frame)
        raw = self.artifacts.booster.predict(matrix)
        return _apply_calibrator(self.artifacts.calibrator, raw)

    def explain(self, row: pd.DataFrame | pd.Series | dict, top_n: int = 8) -> pd.DataFrame:
        """Return local SHAP contributions for a single flight."""
        if isinstance(row, pd.DataFrame):
            single = row.head(1).copy()
        elif isinstance(row, dict):
            single = pd.DataFrame([row])
        else:
            single = row.to_frame().T
        matrix = self.artifacts.builder.transform(single)
        values = self.explainer.shap_values(matrix)
        if isinstance(values, list):
            values = values[-1]
        values = np.asarray(values).reshape(-1)
        contributions = pd.DataFrame(
            {
                "feature": matrix.columns,
                "value": matrix.iloc[0].astype(object).to_numpy(),
                "shap_value": values,
            }
        )
        contributions["abs_shap"] = contributions["shap_value"].abs()
        return contributions.sort_values("abs_shap", ascending=False).head(top_n).drop(columns="abs_shap")


def load_predictor(cfg: Config) -> OTPPredictor:
    logger.info("Loading OTP artifacts for interactive scoring")
    return OTPPredictor(load_otp_artifacts(cfg))
