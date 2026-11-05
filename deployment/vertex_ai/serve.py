"""FastAPI prediction service for Vertex AI custom prediction.

The service loads the OTP artifacts once at startup and exposes ``/health`` and
``/predict``. It is intentionally small: the feature builder and calibrator are the
same objects used in training.

Run locally::

    uvicorn deployment.vertex_ai.serve:app --port 8080
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="FlightOps Insight scoring API", version="0.1.0")


class FlightRequest(BaseModel):
    carrier: str
    origin: str
    dest: str
    aircraft_type: str = "A320"
    flight_date: str = Field(description="ISO date, e.g. 2026-11-15")
    dep_hour: int = Field(ge=0, le=23)
    dep_minute: int = Field(default=0, ge=0, le=59)
    sched_turn_min: int = Field(default=45, ge=0, le=720)
    distance_mi: float = Field(default=900, ge=0)
    crs_elapsed_min: float = Field(default=150, ge=0)
    inbound_known: bool = False
    inbound_arr_delay: float = 0
    inbound_dep_delay: float = 0
    origin_hour_departures: int = Field(default=20, ge=0)
    origin_daily_departures: int = Field(default=250, ge=0)
    route_daily_frequency: int = Field(default=4, ge=0)


@lru_cache(maxsize=1)
def _predictor():
    import sys
    from pathlib import Path

    root = Path(os.environ.get("FLIGHTOPS_ROOT", "/app"))
    if str(root / "src") not in sys.path:
        sys.path.insert(0, str(root / "src"))
    from flightops.config import load_config
    from flightops.models.otp_predict import load_predictor

    return load_predictor(load_config(os.environ.get("FLIGHTOPS_CONFIG")))


def _to_row(request: FlightRequest):
    import pandas as pd

    from flightops.data.aircraft import HUBS, TYPE_ATTRIBUTES, is_holiday

    flight_date = pd.Timestamp(request.flight_date)
    family, seats, min_turn = TYPE_ATTRIBUTES.get(request.aircraft_type, ("narrowbody", 160, 45))
    inbound_known = int(request.inbound_known)
    row: dict[str, Any] = {
        "month": flight_date.month,
        "day_of_week": flight_date.isoweekday(),
        "day_of_year": flight_date.timetuple().tm_yday,
        "dep_hour": request.dep_hour,
        "dep_minute": request.dep_minute,
        "is_weekend": int(flight_date.isoweekday() >= 6),
        "is_holiday": int(is_holiday(pd.Series([flight_date])).iloc[0]),
        "distance_mi": request.distance_mi,
        "crs_elapsed_min": request.crs_elapsed_min,
        "seats": seats,
        "min_turn_min": min_turn,
        "sched_turn_min": request.sched_turn_min,
        "prev_dep_delay_min": request.inbound_dep_delay if inbound_known else float("nan"),
        "prev_arr_delay_obs": request.inbound_arr_delay if inbound_known else float("nan"),
        "inbound_known": inbound_known,
        "prev_cancelled": 0,
        "origin_hour_departures": request.origin_hour_departures,
        "origin_daily_departures": request.origin_daily_departures,
        "route_daily_frequency": request.route_daily_frequency,
        "is_hub_origin": int((request.carrier, request.origin) in {(c, a) for c, airports in HUBS.items() for a in airports}),
        "is_hub_dest": int((request.carrier, request.dest) in {(c, a) for c, airports in HUBS.items() for a in airports}),
        "carrier": request.carrier,
        "origin": request.origin,
        "dest": request.dest,
        "aircraft_type": request.aircraft_type,
        "aircraft_family": family,
        "origin_state": "",
        "dest_state": "",
        "flight_date": flight_date,
    }
    return pd.DataFrame([row])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/predict")
def predict(request: FlightRequest) -> dict[str, Any]:
    try:
        predictor = _predictor()
        row = _to_row(request)
        probability = float(predictor.predict(row)[0])
        explanation = predictor.explain(row, top_n=5)
        return {
            "probability": round(probability, 6),
            "risk_tier": _risk_tier(probability),
            "threshold": round(float(predictor.artifacts.threshold), 4),
            "top_drivers": explanation[["feature", "shap_value"]].to_dict(orient="records"),
            "model_version": os.environ.get("MODEL_VERSION", "prototype"),
        }
    except Exception as exc:  # pragma: no cover - service guard
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _risk_tier(probability: float) -> str:
    if probability >= 0.75:
        return "Critical"
    if probability >= 0.50:
        return "High"
    if probability >= 0.25:
        return "Moderate"
    return "Low"
