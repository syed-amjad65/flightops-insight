"""Run the CP-SAT maintenance scenarios and sensitivity analysis."""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from flightops.config import ensure_directories, load_config, set_global_seed
from flightops.logging_utils import get_logger, log_section
from flightops.optimization.scenarios import run_optimization_scenarios

logger = get_logger("scripts.run_optimization")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-solve even if results exist.")
    args = parser.parse_args()

    cfg = load_config()
    set_global_seed()
    ensure_directories(cfg)

    log_section(logger, "Run maintenance optimization scenarios")
    results = run_optimization_scenarios(cfg, force=args.force)
    base = results["baseline_vs_optimized"]
    logger.info(
        "Greedy $%.0f -> optimized $%.0f (saving %.1f%%)",
        base["greedy_cost_usd"],
        base["optimized_cost_usd"],
        base["savings_vs_greedy_pct"],
    )


if __name__ == "__main__":
    main()
