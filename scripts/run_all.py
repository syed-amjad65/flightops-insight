"""End-to-end pipeline: data -> OTP -> RUL -> maintenance optimization.

Examples
--------
Full rebuild (downloads/uses cached raw data, retrains everything)::

    python scripts/run_all.py --force

Refresh analytics and the app without retraining the models::

    python scripts/run_all.py --skip-train
"""

from __future__ import annotations

import argparse
import time

import _bootstrap  # noqa: F401

from flightops.analysis.otp_insights import build_otp_insights
from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.data.database import build_database
from flightops.data.otp import build_otp_dataset
from flightops.data.rul import build_rul_dataset
from flightops.logging_utils import get_logger, log_section
from flightops.models.otp_model import train_otp_models
from flightops.models.rul_model import train_rul_models
from flightops.optimization.scenarios import run_optimization_scenarios

logger = get_logger("scripts.run_all")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild data and retrain all models.")
    parser.add_argument("--skip-data", action="store_true", help="Do not rebuild raw->processed data.")
    parser.add_argument("--skip-train", action="store_true", help="Reuse existing model artifacts.")
    parser.add_argument("--skip-rul", action="store_true", help="Do not train the RUL models.")
    parser.add_argument("--skip-optimize", action="store_true", help="Do not re-solve maintenance scenarios.")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config()
    set_global_seed()
    ensure_directories(cfg)

    if not args.skip_data:
        log_section(logger, "Stage 1/5: build datasets")
        build_otp_dataset(cfg, force=args.force)
        build_rul_dataset(cfg, force=args.force)
        build_database(cfg, force=args.force)

    if not args.skip_train:
        log_section(logger, "Stage 2/5: train OTP model")
        train_otp_models(cfg, force=args.force)
        if not args.skip_rul:
            log_section(logger, "Stage 3/5: train RUL models")
            train_rul_models(cfg, force=args.force)

    log_section(logger, "Stage 4/5: rebuild OTP insights")
    build_otp_insights(cfg)

    if not args.skip_optimize:
        log_section(logger, "Stage 5/5: maintenance optimization")
        run_optimization_scenarios(cfg, force=args.force)

    logger.info("Pipeline finished in %.1f seconds", time.perf_counter() - started)


if __name__ == "__main__":
    main()
