"""Console entrypoints shared by local scripts and managed job runners."""

from __future__ import annotations

import argparse

from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.logging_utils import get_logger

logger = get_logger("cli")


def train_otp() -> None:
    parser = argparse.ArgumentParser(description="Train OTP models")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    from flightops.data.database import build_database
    from flightops.data.otp import build_otp_dataset
    from flightops.models.otp_model import train_otp_models
    from flightops.analysis.otp_insights import build_otp_insights

    cfg = load_config(args.config)
    set_global_seed()
    ensure_directories(cfg)
    if not cfg.path("data.otp.parquet").exists():
        build_otp_dataset(cfg)
        build_database(cfg)
    artifacts = train_otp_models(cfg, force=args.force)
    build_otp_insights(cfg)
    logger.info("OTP training complete; threshold=%.4f", artifacts.threshold)


def train_rul() -> None:
    parser = argparse.ArgumentParser(description="Train RUL models")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    from flightops.data.rul import build_rul_dataset
    from flightops.models.rul_model import train_rul_models

    cfg = load_config(args.config)
    set_global_seed()
    ensure_directories(cfg)
    if not cfg.path("data.rul.parquet").exists():
        build_rul_dataset(cfg)
    artifacts = train_rul_models(cfg, force=args.force)
    logger.info("RUL training complete; LSTM RMSE=%.2f", artifacts.metrics["models"]["lstm"]["rmse"])


def run_optimization() -> None:
    parser = argparse.ArgumentParser(description="Run maintenance scenarios")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    from flightops.optimization.scenarios import run_optimization_scenarios

    cfg = load_config(args.config)
    set_global_seed()
    ensure_directories(cfg)
    run_optimization_scenarios(cfg, force=args.force)
