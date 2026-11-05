"""Typed I/O helpers used across the project."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import joblib
import pandas as pd


def write_json(payload: Any, path: str | Path, indent: int = 2) -> Path:
    """Write JSON with UTF-8 encoding, creating parent directories."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=indent, default=str)
    return destination


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_parquet(frame: pd.DataFrame, path: str | Path, **kwargs: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(destination, index=False, **kwargs)
    return destination


def read_parquet(path: str | Path, columns: list[str] | None = None) -> pd.DataFrame:
    return pd.read_parquet(Path(path), columns=columns)


def save_joblib(obj: Any, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(obj, destination, compress=3)
    return destination


def load_joblib(path: str | Path) -> Any:
    return joblib.load(Path(path))


@contextmanager
def timer(label: str) -> Iterator[dict[str, float]]:
    """Context manager that records elapsed seconds in a mutable dict."""
    box: dict[str, float] = {}
    started = time.perf_counter()
    try:
        yield box
    finally:
        box["elapsed_s"] = round(time.perf_counter() - started, 3)
