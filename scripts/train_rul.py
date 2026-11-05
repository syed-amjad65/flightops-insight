"""Train the C-MAPSS RUL models and refresh the watchlist and cost table."""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.data.rul import build_rul_dataset
from flightops.logging_utils import get_logger, log_section
from flightops.models.rul_model import train_rul_models

logger = get_logger("scripts.train_rul")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Retrain even if artifacts exist.")
    parser.add_argument("--rebuild-data", action="store_true", help="Rebuild the C-MAPSS parquet first.")
    args = parser.parse_args()

    cfg = load_config()
    set_global_seed()
    ensure_directories(cfg)

    if args.rebuild_data or not cfg.path("data.rul.parquet").exists():
        build_rul_dataset(cfg, force=args.rebuild_data)

    log_section(logger, "Train RUL models")
    artifacts = train_rul_models(cfg, force=args.force)
    logger.info("LSTM RMSE: %.2f cycles", artifacts.metrics["models"]["lstm"]["rmse"])


if __name__ == "__main__":
    main()
