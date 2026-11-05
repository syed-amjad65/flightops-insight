"""Databricks job entrypoint for OTP training.

The wheel exposes this as ``flightops_train_otp``. The job expects the repository
package and configuration to be available on the cluster; data paths come from
``config.yaml`` or environment overrides.
"""

from __future__ import annotations

import argparse

from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.data.database import build_database
from flightops.data.otp import build_otp_dataset
from flightops.logging_utils import get_logger
from flightops.models.otp_model import train_otp_models

logger = get_logger("databricks.train_otp")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    set_global_seed()
    ensure_directories(cfg)
    if not cfg.path("data.otp.parquet").exists():
        build_otp_dataset(cfg)
        build_database(cfg)
    artifacts = train_otp_models(cfg, force=args.force)
    logger.info("OTP training complete; threshold=%.4f", artifacts.threshold)


if __name__ == "__main__":
    main()
