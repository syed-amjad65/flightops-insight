"""NASA C-MAPSS turbofan degradation data (FD001 by default).

Source: NASA Prognostics Center of Excellence, C-MAPSS Turbofan Engine
Degradation Simulation Data Set.  The raw files are public.  A GitHub mirror is
used only if the files are missing locally; the checksum/structure is validated
on load.

Provenance is recorded in the model card.  FD001 has one operating condition
and one fault mode, 100 training units run to failure, and 100 test units
truncated at an unknown point with a published RUL ground truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from flightops.config import Config
from flightops.data.contracts import validate_rul
from flightops.data.providers import RULDataProvider
from flightops.io import write_parquet
from flightops.logging_utils import get_logger

logger = get_logger("data.rul")

RUL_COLUMNS = (
    ["unit", "cycle"]
    + [f"op_setting_{i}" for i in range(1, 4)]
    + [f"sensor_{i}" for i in range(1, 22)]
)


def _ensure_file(cfg: Config, filename: str) -> Path:
    destination = cfg.path("paths.data_raw") / filename
    if destination.exists() and destination.stat().st_size > 100:
        return destination
    base = cfg.get("data.rul.mirror_base")
    url = base.rstrip("/") + "/" + filename
    logger.info("Downloading C-MAPSS file %s", url)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    destination.write_bytes(response.content)
    return destination


def _read_trajectory(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=r"\s+", header=None, engine="python")
    if frame.shape[1] != 26:
        raise ValueError(f"Expected 26 columns in {path}, found {frame.shape[1]}")
    frame.columns = RUL_COLUMNS
    frame["unit"] = frame["unit"].astype("int32")
    frame["cycle"] = frame["cycle"].astype("int32")
    return frame


def load_cmapss_frame(cfg: Config) -> pd.DataFrame:
    """Load FD001 train/test and attach the published RUL target."""
    raw_dir = cfg.path("paths.data_raw")
    subset = str(cfg.get("data.rul.subset", "FD001"))
    train = _read_trajectory(_ensure_file(cfg, f"train_{subset}.txt"))
    test = _read_trajectory(_ensure_file(cfg, f"test_{subset}.txt"))
    rul_file = _ensure_file(cfg, f"RUL_{subset}.txt")
    last_rul = pd.read_csv(rul_file, header=None)[0].to_numpy(dtype=float)

    max_cycle = train.groupby("unit")["cycle"].max()
    train["rul"] = (train["unit"].map(max_cycle) - train["cycle"]).astype(float)
    train["split"] = "train"

    test_max = test.groupby("unit")["cycle"].max().sort_index()
    if len(last_rul) != len(test_max):
        raise ValueError("RUL file length does not match number of test units")
    last_rul_map = dict(zip(test_max.index, last_rul))
    test["rul"] = test.apply(
        lambda row: last_rul_map[row["unit"]] + (test_max[row["unit"]] - row["cycle"]), axis=1
    )
    test["split"] = "test"

    frame = pd.concat([train, test], ignore_index=True)
    frame["rul"] = frame["rul"].astype("float64")
    frame["split"] = frame["split"].astype("string")
    validate_rul(frame)
    logger.info(
        "C-MAPSS %s: %d train units, %d test units, %d rows",
        subset,
        train["unit"].nunique(),
        test["unit"].nunique(),
        len(frame),
    )
    return frame


class CMAPSSProvider(RULDataProvider):
    name = "nasa_cmapss_fd001"

    def load(self, cfg: Config) -> pd.DataFrame:
        path = cfg.path("data.rul.parquet")
        if path.exists():
            return pd.read_parquet(path)
        frame = load_cmapss_frame(cfg)
        write_parquet(frame, path, compression="zstd")
        return frame


class SyntheticRULProvider(RULDataProvider):
    """Fallback only; not used for reported results.

    Generates exponential degradation trajectories with unit-specific rates so
    every downstream interface can be exercised without a download.
    """

    name = "synthetic_degradation_fallback"

    def load(self, cfg: Config) -> pd.DataFrame:
        rng = np.random.default_rng(int(cfg.get("project.seed", 42)))
        rows: list[pd.DataFrame] = []
        for split, n_units in (("train", 80), ("test", 20)):
            for unit in range(1, n_units + 1):
                life = int(rng.integers(140, 320)) if split == "train" else int(rng.integers(140, 320))
                observed = life if split == "train" else int(rng.integers(60, life))
                cycles = np.arange(1, observed + 1)
                wear = cycles / life
                record: dict[str, Any] = {
                    "unit": np.full(observed, unit),
                    "cycle": cycles,
                    "split": np.full(observed, split),
                    "rul": np.full(observed, float(max(0, life - observed)) if split == "test" else 0.0),
                }
                # Training rows need RUL decreasing to zero; test rows only need
                # the published last-cycle RUL, but a decreasing proxy keeps the
                # schema useful for smoke tests.
                record["rul"] = (life - cycles).astype(float)
                for i in range(1, 4):
                    record[f"op_setting_{i}"] = np.zeros(observed)
                for i in range(1, 22):
                    record[f"sensor_{i}"] = (
                        10.0
                        + 0.02 * i * cycles
                        + 3.0 * np.exp(4.0 * wear)
                        + rng.normal(0, 0.2, size=observed)
                    )
                rows.append(pd.DataFrame(record))
        frame = pd.concat(rows, ignore_index=True)
        frame["split"] = frame["split"].astype("string")
        validate_rul(frame)
        return frame


def build_rul_dataset(cfg: Config, force: bool = False) -> Path:
    destination = cfg.path("data.rul.parquet")
    if destination.exists() and not force:
        logger.info("RUL parquet already exists: %s", destination)
        return destination
    source = str(cfg.get("data.rul.source", "cmapss")).lower()
    frame = SyntheticRULProvider().load(cfg) if source == "synthetic" else load_cmapss_frame(cfg)
    write_parquet(frame, destination, compression="zstd")
    logger.info("Wrote %d RUL rows to %s", len(frame), destination)
    return destination


def load_rul(cfg: Config) -> pd.DataFrame:
    path = cfg.path("data.rul.parquet")
    if not path.exists():
        build_rul_dataset(cfg)
    return pd.read_parquet(path)
