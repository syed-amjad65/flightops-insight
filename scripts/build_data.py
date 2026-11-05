"""Build the BTS OTP parquet, C-MAPSS parquet and DuckDB analytics database."""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401  (adds src to sys.path)

from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.data.database import build_database
from flightops.data.otp import build_otp_dataset
from flightops.data.rul import build_rul_dataset
from flightops.logging_utils import get_logger, log_section

logger = get_logger("scripts.build_data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild even if processed files exist.")
    parser.add_argument("--skip-otp", action="store_true", help="Leave the BTS parquet untouched.")
    parser.add_argument("--skip-rul", action="store_true", help="Leave the C-MAPSS parquet untouched.")
    args = parser.parse_args()

    cfg = load_config()
    set_global_seed()
    ensure_directories(cfg)

    if not args.skip_otp:
        log_section(logger, "Build BTS OTP dataset")
        logger.info("Wrote %s", build_otp_dataset(cfg, force=args.force))
    if not args.skip_rul:
        log_section(logger, "Build C-MAPSS dataset")
        logger.info("Wrote %s", build_rul_dataset(cfg, force=args.force))
    log_section(logger, "Build DuckDB analytics database")
    logger.info("Wrote %s", build_database(cfg, force=args.force or not args.skip_otp))


if __name__ == "__main__":
    main()
