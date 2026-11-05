"""Feature engineering for C-MAPSS remaining-useful-life models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from flightops.config import Config

SENSOR_COLUMNS = [f"sensor_{i}" for i in range(1, 22)]
SETTING_COLUMNS = [f"op_setting_{i}" for i in range(1, 4)]


def select_informative_sensors(frame: pd.DataFrame, tolerance: float = 1e-6) -> list[str]:
    """Drop sensors that are constant in the training set (FD001 has 7)."""
    candidate = [c for c in SENSOR_COLUMNS if frame[c].std() > tolerance]
    return candidate


@dataclass
class RULScaler:
    features: list[str] = field(default_factory=list)
    mean: dict[str, float] = field(default_factory=dict)
    std: dict[str, float] = field(default_factory=dict)

    def fit(self, frame: pd.DataFrame, features: list[str]) -> "RULScaler":
        self.features = list(features)
        for feature in features:
            values = frame[feature].astype(float)
            self.mean[feature] = float(values.mean())
            self.std[feature] = float(values.std(ddof=0)) or 1.0
        return self

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        matrix = np.column_stack(
            [
                (frame[feature].astype(float).to_numpy() - self.mean[feature]) / self.std[feature]
                for feature in self.features
            ]
        )
        return matrix.astype(np.float32)

    def to_json(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"features": self.features, "mean": self.mean, "std": self.std}, indent=2),
            encoding="utf-8",
        )
        return destination

    @classmethod
    def from_json(cls, path: str | Path) -> "RULScaler":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(features=payload["features"], mean=payload["mean"], std=payload["std"])


def windows_for_unit(
    unit_frame: pd.DataFrame,
    features: list[str],
    window: int,
    target_cap: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create chronological rolling windows for one engine.

    Returns arrays shaped (n_windows, window, n_features), targets and the
    cycle index at the end of each window.
    """
    unit_frame = unit_frame.sort_values("cycle")
    values = unit_frame[features].to_numpy(dtype=np.float32)
    rul = np.minimum(unit_frame["rul"].to_numpy(dtype=np.float32), target_cap)
    cycles = unit_frame["cycle"].to_numpy()
    sequences, targets, end_cycles = [], [], []
    for end in range(window - 1, len(unit_frame)):
        sequences.append(values[end - window + 1 : end + 1])
        targets.append(rul[end])
        end_cycles.append(cycles[end])
    if not sequences:
        return (
            np.empty((0, window, len(features)), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )
    return np.stack(sequences), np.asarray(targets, dtype=np.float32), np.asarray(end_cycles)


def build_sequence_dataset(
    frame: pd.DataFrame,
    features: list[str],
    window: int,
    target_cap: float,
    units: list[int],
    last_window_only: bool = False,
    stride: int = 1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build sequence tensors for a set of engines."""
    sequences, targets, end_cycles, unit_ids = [], [], [], []
    for unit in units:
        unit_frame = frame[frame["unit"] == unit]
        x, y, c = windows_for_unit(unit_frame, features, window, target_cap)
        if len(x) == 0:
            continue
        if last_window_only:
            x, y, c = x[-1:], y[-1:], c[-1:]
        else:
            x, y, c = x[::stride], y[::stride], c[::stride]
        sequences.append(x)
        targets.append(y)
        end_cycles.append(c)
        unit_ids.append(np.full(len(x), unit, dtype=np.int32))
    if not sequences:
        empty_x = np.empty((0, window, len(features)), dtype=np.float32)
        return empty_x, np.array([], dtype=np.float32), np.array([], dtype=np.int32), np.array([], dtype=np.int32)
    return (
        np.concatenate(sequences),
        np.concatenate(targets),
        np.concatenate(end_cycles),
        np.concatenate(unit_ids),
    )


def window_summary_features(sequences: np.ndarray, feature_names: list[str]) -> np.ndarray:
    """Summarise each window with last/mean/std/min/max/slope per sensor."""
    n, window, n_features = sequences.shape
    x = np.arange(window, dtype=np.float32)
    x_centered = x - x.mean()
    denom = float((x_centered**2).sum()) or 1.0
    summaries = []
    last = sequences[:, -1, :]
    mean = sequences.mean(axis=1)
    std = sequences.std(axis=1)
    minimum = sequences.min(axis=1)
    maximum = sequences.max(axis=1)
    slopes = np.einsum("nwf,w->nf", sequences, x_centered) / denom
    summaries = np.concatenate([last, mean, std, minimum, maximum, slopes], axis=1)
    return summaries.astype(np.float32)


def summary_feature_names(feature_names: list[str]) -> list[str]:
    prefixes = ["last", "mean", "std", "min", "max", "slope"]
    return [f"{prefix}_{name}" for prefix in prefixes for name in feature_names]


def unit_train_val_split(
    units: list[int], val_fraction: float, seed: int
) -> tuple[list[int], list[int]]:
    """Deterministic unit-level split; no engine appears in both sets."""
    rng = np.random.default_rng(seed)
    shuffled = np.array(sorted(units))
    rng.shuffle(shuffled)
    n_val = max(1, int(round(len(shuffled) * val_fraction)))
    return sorted(shuffled[n_val:].tolist()), sorted(shuffled[:n_val].tolist())


def load_rul_scaler(cfg: Config) -> RULScaler:
    return RULScaler.from_json(cfg.path("paths.encoders") / "rul_scaler.json")
