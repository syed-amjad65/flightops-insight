"""Configuration loading, path resolution and reproducibility utilities.

The project deliberately keeps every threshold, cost and hyper-parameter in
``config.yaml``.  This module is the only place that knows how to turn that
file into typed, absolute paths.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class Config:
    """Small mapping wrapper with dotted access and absolute path helpers."""

    values: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Return a nested value using ``"section.key"`` notation."""
        node: Any = self.values
        for part in dotted_key.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, dotted_key: str, default: str | None = None) -> Path:
        """Resolve a path from the config relative to the repository root."""
        raw = self.get(dotted_key, default)
        if raw is None:
            raise KeyError(f"Path key not found in config: {dotted_key}")
        candidate = Path(str(raw))
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate

    def with_overrides(self, overrides: Mapping[str, Any]) -> "Config":
        """Deep-merge a small override mapping (used by tests and scenarios)."""
        merged = _deep_merge(dict(self.values), overrides)
        return Config(merged)


def _deep_merge(base: dict[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def load_config(
    path: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Load ``config.yaml`` and apply optional nested overrides."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}
    if overrides:
        values = _deep_merge(values, overrides)
    return Config(values)


def set_global_seed(seed: int | None = None) -> int:
    """Seed Python, NumPy and PyTorch. Returns the seed actually used."""
    cfg_seed = seed
    if cfg_seed is None:
        cfg_seed = int(load_config().get("project.seed", 42))
    os.environ["PYTHONHASHSEED"] = str(cfg_seed)
    random.seed(cfg_seed)
    np.random.seed(cfg_seed)
    try:  # torch is optional for non-deep-learning workflows
        import torch

        torch.manual_seed(cfg_seed)
        torch.cuda.manual_seed_all(cfg_seed)
        torch.use_deterministic_algorithms(False)  # CPU LSTM kernels are stable
    except ImportError:  # pragma: no cover - torch is in requirements
        pass
    return cfg_seed


def ensure_directories(cfg: Config) -> None:
    """Create every output directory referenced by the config."""
    for key in (
        "data_raw",
        "data_processed",
        "data_synthetic",
        "artifacts",
        "models",
        "encoders",
        "reports",
        "figures",
        "metrics",
        "optimization",
        "docs",
    ):
        cfg.path(f"paths.{key}").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "reports" / "logs").mkdir(parents=True, exist_ok=True)
