"""Train and calibrate the OTP delay-risk models, then rebuild the OTP insights."""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from flightops.analysis.otp_insights import build_otp_insights
from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.data.database import build_database
from flightops.data.otp import build_otp_dataset
from flightops.logging_utils import get_logger, log_section
from flightops.models.otp_model import train_otp_models

logger = get_logger("scripts.train_otp")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Retrain even if artifacts exist.")
    parser.add_argument("--rebuild-data", action="store_true", help="Rebuild the BTS parquet and DuckDB first.")
    args = parser.parse_args()

    cfg = load_config()
    set_global_seed()
    ensure_directories(cfg)

    if args.rebuild_data:
        build_otp_dataset(cfg, force=True)
        build_database(cfg, force=True)
    elif not cfg.path("data.otp.parquet").exists():
        build_otp_dataset(cfg)
        build_database(cfg)

    log_section(logger, "Train OTP models")
    artifacts = train_otp_models(cfg, force=args.force)
    logger.info("Operating threshold: %.4f", artifacts.threshold)

    log_section(logger, "Build OTP insights")
    build_otp_insights(cfg)


if __name__ == "__main__":
    main()
