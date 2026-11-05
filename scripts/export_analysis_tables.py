"""Export compact CSV tables used by the R notebook and lightweight notebooks.

The full datasets stay in Parquet/DuckDB; these extracts are small enough to
version or inspect and contain no row-level personal data.
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

import numpy as np
import pandas as pd

from flightops.config import load_config
from flightops.data.database import query_df
from flightops.logging_utils import get_logger

logger = get_logger("scripts.export_analysis")


def _otp_tables(cfg) -> None:
    out = cfg.path("paths.metrics")

    query_df(
        cfg,
        """
        SELECT dep_hour, flights, delay_rate, avg_dep_delay_min, avg_inbound_delay_min
        FROM otp_hour ORDER BY dep_hour
        """,
    ).to_csv(out / "r_otp_hourly.csv", index=False)

    query_df(
        cfg,
        """
        SELECT CAST(flight_date AS DATE) AS flight_date,
               MONTH(flight_date) AS month, flights,
               delayed_flights, delay_rate, avg_dep_delay_min
        FROM otp_daily ORDER BY flight_date
        """,
    ).to_csv(out / "r_otp_daily.csv", index=False)

    query_df(
        cfg,
        """
        SELECT month, flights, delay_rate, otp_rate, avg_dep_delay_min
        FROM otp_monthly ORDER BY month
        """,
    ).to_csv(out / "r_otp_monthly.csv", index=False)

    query_df(
        cfg,
        """
        SELECT
            CASE
                WHEN sched_turn_min IS NULL THEN 'No inbound link'
                WHEN sched_turn_min < 40 THEN '< 40 min'
                WHEN sched_turn_min < 60 THEN '40-59 min'
                WHEN sched_turn_min < 90 THEN '60-89 min'
                WHEN sched_turn_min < 150 THEN '90-149 min'
                ELSE '150+ min'
            END AS turn_bucket,
            COUNT(*) AS flights,
            SUM(CASE WHEN dep_del15 = 1 THEN 1 ELSE 0 END) AS delayed,
            SUM(CASE WHEN dep_del15 = 0 THEN 1 ELSE 0 END) AS on_time
        FROM flights
        WHERE dep_del15 IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
    ).to_csv(out / "r_otp_turnaround.csv", index=False)

    query_df(
        cfg,
        """
        SELECT
            CASE
                WHEN inbound_known = 0 THEN 'Inbound not yet known'
                WHEN prev_arr_delay_obs < 0 THEN 'Inbound early'
                WHEN prev_arr_delay_obs < 15 THEN 'Inbound 0-14 min late'
                WHEN prev_arr_delay_obs < 30 THEN 'Inbound 15-29 min late'
                WHEN prev_arr_delay_obs < 60 THEN 'Inbound 30-59 min late'
                ELSE 'Inbound 60+ min late'
            END AS segment,
            COUNT(*) AS flights,
            SUM(CASE WHEN dep_del15 = 1 THEN 1 ELSE 0 END) AS delayed,
            SUM(CASE WHEN dep_del15 = 0 THEN 1 ELSE 0 END) AS on_time
        FROM flights
        WHERE dep_del15 IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
    ).to_csv(out / "r_otp_inbound.csv", index=False)
    logger.info("Wrote OTP analysis extracts to %s", out)


def _rul_survival_table(cfg) -> None:
    frame = pd.read_parquet(cfg.path("data.rul.parquet"))
    watchlist = pd.read_csv(cfg.path("paths.metrics") / "rul_watchlist.csv")
    test_rul = watchlist.set_index("unit")["true_rul_cycles"].to_dict()
    threshold = int(cfg.get("rul.alert_threshold_cycles", 20))

    rows = []
    for (split, unit), group in frame.groupby(["split", "unit"]):
        group = group.sort_values("cycle")
        last_cycle = int(group["cycle"].max())
        first = group.head(30)
        cycles = first["cycle"].to_numpy(dtype=float)
        slope_4 = float(np.polyfit(cycles, first["sensor_4"].to_numpy(dtype=float), 1)[0])
        slope_11 = float(np.polyfit(cycles, first["sensor_11"].to_numpy(dtype=float), 1)[0])
        if split == "train":
            duration = last_cycle - threshold + 1
            event = 1
        else:
            rul = float(test_rul[unit])
            if rul <= threshold:
                duration = last_cycle - (threshold - rul)
                event = 1
            else:
                duration = last_cycle
                event = 0
        rows.append(
            {
                "unit": int(unit),
                "split": split,
                "duration": max(duration, 1),
                "event": event,
                "last_cycle": last_cycle,
                "init_sensor_4": float(first["sensor_4"].iloc[0]),
                "init_sensor_11": float(first["sensor_11"].iloc[0]),
                "slope_sensor_4": slope_4,
                "slope_sensor_11": slope_11,
            }
        )
    out = pd.DataFrame(rows).sort_values(["split", "unit"])
    out.to_csv(cfg.path("paths.metrics") / "r_rul_survival.csv", index=False)
    logger.info("Wrote survival table: %d units, %d events", len(out), int(out["event"].sum()))


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    cfg = load_config()
    _otp_tables(cfg)
    _rul_survival_table(cfg)


if __name__ == "__main__":
    main()
