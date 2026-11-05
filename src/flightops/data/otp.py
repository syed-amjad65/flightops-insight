"""BTS On-Time Performance ingestion and synthetic fallback.

Provenance
----------
Primary source: U.S. Bureau of Transportation Statistics (BTS) On-Time
Performance, 2024 monthly PREZIP extracts (public domain).  The code downloads
one zip per month, extracts the CSV and builds a single typed parquet table.
If the download is unavailable, ``SyntheticOTPProvider`` emits the same
canonical schema so every downstream module still runs.

Aircraft type is **not** present in BTS.  See ``flightops.data.aircraft`` for
the documented deterministic enrichment.  No proprietary or Skywise data is
used.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

from flightops.config import Config, PROJECT_ROOT
from flightops.data.aircraft import enrich_aircraft, is_holiday
from flightops.data.contracts import validate_otp
from flightops.data.providers import OTPDataProvider
from flightops.io import write_parquet
from flightops.logging_utils import get_logger

logger = get_logger("data.otp")

# BTS 2024 files use the legacy column names (IATA_CODE_Reporting_Airline).
SELECT_SQL = """
WITH raw AS (
    SELECT * FROM read_csv_auto({sources}, header=true, union_by_name=true, sample_size=200000)
), base AS (
    SELECT
        CAST(FlightDate AS DATE) AS flight_date,
        CAST(Month AS SMALLINT) AS flight_month,
        CAST(DayOfWeek AS TINYINT) AS day_of_week,
        CAST(dayofyear(CAST(FlightDate AS DATE)) AS SMALLINT) AS day_of_year,
        CAST(IATA_CODE_Reporting_Airline AS VARCHAR) AS carrier,
        NULLIF(TRIM(Tail_Number), '') AS tail_number,
        CAST(Flight_Number_Reporting_Airline AS INTEGER) AS flight_number,
        CAST(Origin AS VARCHAR) AS origin,
        CAST(Dest AS VARCHAR) AS dest,
        CAST(OriginState AS VARCHAR) AS origin_state,
        CAST(DestState AS VARCHAR) AS dest_state,
        CAST(CRSDepTime AS INTEGER) AS crs_dep_raw,
        CAST(CRSArrTime AS INTEGER) AS crs_arr_raw,
        CAST(DepDelay AS DOUBLE) AS dep_delay_min,
        CAST(DepDel15 AS TINYINT) AS dep_del15,
        CAST(ArrDelay AS DOUBLE) AS arr_delay_min,
        CAST(ArrDel15 AS TINYINT) AS arr_del15,
        CAST(COALESCE(Cancelled, 0) AS TINYINT) AS cancelled,
        CAST(COALESCE(Diverted, 0) AS TINYINT) AS diverted,
        CAST(Distance AS DOUBLE) AS distance_mi,
        CAST(CRSElapsedTime AS DOUBLE) AS crs_elapsed_min,
        CAST(ActualElapsedTime AS DOUBLE) AS actual_elapsed_min,
        CAST(AirTime AS DOUBLE) AS air_time_min,
        CAST(TaxiOut AS DOUBLE) AS taxi_out_min,
        CAST(TaxiIn AS DOUBLE) AS taxi_in_min,
        CAST(CarrierDelay AS DOUBLE) AS carrier_delay_min,
        CAST(WeatherDelay AS DOUBLE) AS weather_delay_min,
        CAST(NASDelay AS DOUBLE) AS nas_delay_min,
        CAST(SecurityDelay AS DOUBLE) AS security_delay_min,
        CAST(LateAircraftDelay AS DOUBLE) AS late_aircraft_delay_min
    FROM raw
    WHERE CAST(CRSDepTime AS INTEGER) BETWEEN 0 AND 2400
      AND CAST(CRSArrTime AS INTEGER) BETWEEN 0 AND 2400
      AND CAST(CRSElapsedTime AS DOUBLE) > 0
), times AS (
    SELECT *,
        ((crs_dep_raw // 100) * 60 + crs_dep_raw % 100) AS crs_dep_min,
        ((crs_arr_raw // 100) * 60 + crs_arr_raw % 100) AS crs_arr_min,
        CAST(flight_date AS TIMESTAMP)
            + (((crs_dep_raw // 100) * 60 + crs_dep_raw % 100) * INTERVAL 1 MINUTE) AS sched_dep_ts
    FROM base
), times2 AS (
    SELECT *,
        CAST(flight_date AS TIMESTAMP) + (crs_arr_min * INTERVAL 1 MINUTE)
        + (
            CASE
                WHEN crs_arr_min < crs_dep_min AND (crs_dep_min - crs_arr_min) > 360
                THEN INTERVAL 1 DAY
                ELSE INTERVAL 0 DAY
            END
        ) AS sched_arr_ts
    FROM times
), times3 AS (
    SELECT *,
        sched_dep_ts + dep_delay_min * INTERVAL 1 MINUTE AS actual_dep_ts,
        sched_arr_ts + arr_delay_min * INTERVAL 1 MINUTE AS actual_arr_ts
    FROM times2
), lagged AS (
    SELECT *,
        LAG(sched_arr_ts) OVER w AS prev_sched_arr_ts,
        LAG(actual_arr_ts) OVER w AS prev_actual_arr_ts,
        LAG(dep_delay_min) OVER w AS prev_dep_delay_min,
        LAG(arr_delay_min) OVER w AS prev_arr_delay_min,
        LAG(cancelled) OVER w AS prev_cancelled,
        LAG(dest) OVER w AS prev_dest
    FROM times3
    WINDOW w AS (PARTITION BY tail_number ORDER BY sched_dep_ts, flight_number)
)
SELECT
    *,
    CAST((crs_dep_min // 60) % 24 AS TINYINT) AS dep_hour,
    CAST(crs_dep_min % 60 AS TINYINT) AS dep_minute,
    CASE
        WHEN prev_dest = origin AND date_diff('minute', prev_sched_arr_ts, sched_dep_ts) BETWEEN 15 AND 720
        THEN date_diff('minute', prev_sched_arr_ts, sched_dep_ts)
    END AS sched_turn_min,
    CASE
        WHEN prev_dest = origin
         AND prev_actual_arr_ts IS NOT NULL
         AND prev_actual_arr_ts <= sched_dep_ts - ({cutoff} * INTERVAL 1 MINUTE)
        THEN prev_arr_delay_min
    END AS prev_arr_delay_obs,
    CASE
        WHEN prev_dest = origin
         AND prev_actual_arr_ts IS NOT NULL
         AND prev_actual_arr_ts <= sched_dep_ts - ({cutoff} * INTERVAL 1 MINUTE)
        THEN 1 ELSE 0
    END AS inbound_known,
    COUNT(*) OVER (
        PARTITION BY origin, flight_date, CAST((crs_dep_min // 60) % 24 AS TINYINT)
    ) AS origin_hour_departures,
    COUNT(*) OVER (PARTITION BY origin, flight_date) AS origin_daily_departures,
    COUNT(*) OVER (PARTITION BY origin, dest, flight_date) AS route_daily_frequency
FROM lagged
"""


def _bts_zip_path(cfg: Config, month: int) -> Path:
    year = int(cfg.get("data.otp.year", 2024))
    return cfg.path("paths.data_raw") / f"bts_{year}_{month:02d}.zip"


def _download_month(cfg: Config, month: int) -> Path:
    """Download one BTS monthly zip, validating any existing partial file."""
    path = _bts_zip_path(cfg, month)
    if path.exists() and zipfile.is_zipfile(path):
        return path
    year = int(cfg.get("data.otp.year", 2024))
    template = cfg.get("data.otp.base_url")
    url = template.format(year=year, month=month)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading BTS %s-%02d from %s", year, month, url)
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        temp = path.with_suffix(".zip.part")
        with temp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if chunk:
                    handle.write(chunk)
        temp.replace(path)
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"Downloaded file is not a valid zip: {path}")
    return path


def _extract_month(cfg: Config, month: int) -> Path:
    """Extract the single CSV member once; return its path."""
    year = int(cfg.get("data.otp.year", 2024))
    destination = cfg.path("paths.data_raw") / "extracted" / f"otp_{year}_{month:02d}.csv"
    if destination.exists() and destination.stat().st_size > 1_000_000:
        return destination
    archive = _download_month(cfg, month)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if not members:
            raise RuntimeError(f"No CSV found in {archive}")
        with zf.open(members[0]) as source, destination.open("wb") as target:
            while True:
                chunk = source.read(1 << 22)
                if not chunk:
                    break
                target.write(chunk)
    return destination


def load_bts_frame(cfg: Config, months: list[int] | None = None) -> pd.DataFrame:
    """Load and conform BTS monthly files into the canonical flight schema."""
    months = months or list(cfg.get("data.otp.months", range(1, 13)))
    extracted = [_extract_month(cfg, month) for month in months]
    sources = "[" + ", ".join(f"'{path.as_posix()}'" for path in extracted) + "]"
    cutoff = int(cfg.get("otp.prediction_cutoff_min", 90))
    query = SELECT_SQL.format(sources=sources, cutoff=cutoff)
    logger.info("Querying %d BTS files with DuckDB", len(extracted))
    frame = duckdb.connect().execute(query).df()
    frame = frame.rename(columns={"flight_month": "month"})
    frame["flight_date"] = pd.to_datetime(frame["flight_date"])

    # Keep carriers with a meaningful operating footprint; tiny carriers create
    # unstable categorical splits and are not the target of this analysis.
    counts = frame["carrier"].value_counts()
    keep = counts[counts >= 1000].index
    dropped = len(counts) - len(keep)
    if dropped:
        logger.info("Dropping %d carriers with <1,000 flights", dropped)
    frame = frame[frame["carrier"].isin(keep)].copy()

    frame = enrich_aircraft(frame)
    frame["is_holiday"] = is_holiday(frame["flight_date"]).astype("int8")
    frame["is_weekend"] = frame["day_of_week"].isin([6, 7]).astype("int8")
    frame["route"] = frame["origin"].astype(str) + "_" + frame["dest"].astype(str)
    frame["dep_del15"] = frame["dep_del15"].astype("Int8")
    frame["arr_del15"] = frame["arr_del15"].astype("Int8")
    frame["cancelled"] = frame["cancelled"].astype("int8")
    frame["diverted"] = frame["diverted"].astype("int8")
    frame["inbound_known"] = frame["inbound_known"].astype("int8")
    frame["prev_cancelled"] = frame["prev_cancelled"].fillna(-1).astype("int8")

    # Keep the canonical plus diagnostics needed by the optimizer/app.
    keep_columns = [
        "flight_date", "month", "day_of_week", "day_of_year", "carrier", "tail_number",
        "flight_number", "origin", "dest", "route", "origin_state", "dest_state",
        "sched_dep_ts", "sched_arr_ts", "actual_dep_ts", "actual_arr_ts",
        "dep_hour", "dep_minute", "dep_delay_min", "dep_del15", "arr_delay_min",
        "arr_del15", "cancelled", "diverted", "distance_mi", "crs_elapsed_min",
        "actual_elapsed_min", "air_time_min", "taxi_out_min", "taxi_in_min",
        "carrier_delay_min", "weather_delay_min", "nas_delay_min", "security_delay_min",
        "late_aircraft_delay_min", "aircraft_type", "aircraft_family", "seats",
        "min_turn_min", "sched_turn_min", "prev_dep_delay_min", "prev_arr_delay_obs",
        "inbound_known", "prev_cancelled", "origin_hour_departures",
        "origin_daily_departures", "route_daily_frequency", "is_holiday",
        "is_weekend", "is_hub_origin", "is_hub_dest",
    ]
    frame = frame[[column for column in keep_columns if column in frame.columns]]
    validate_otp(frame)
    return frame


class BTSOTPProvider(OTPDataProvider):
    name = "bts_2024_public"

    def load(self, cfg: Config) -> pd.DataFrame:
        path = cfg.path("data.otp.parquet")
        if path.exists():
            logger.info("Loading cached OTP parquet: %s", path)
            return pd.read_parquet(path)
        frame = load_bts_frame(cfg)
        write_parquet(frame, path, compression="zstd")
        return frame


class SyntheticOTPProvider(OTPDataProvider):
    """Documented synthetic fallback with the same canonical schema.

    The generator is intentionally simple and reproducible. It is *not* used
    for the reported BTS results; it exists so the pipeline can run offline.
    """

    name = "synthetic_fallback"

    def load(self, cfg: Config) -> pd.DataFrame:
        seed = int(cfg.get("project.seed", 42))
        n = int(cfg.get("data.synthetic.n_flights", 250_000))
        rng = np.random.default_rng(seed)
        airports = ["ATL", "DFW", "ORD", "DEN", "LAX", "JFK", "MCO", "LAS", "SEA", "BOS", "PHX", "MIA"]
        weights = np.array([10, 9, 9, 8, 8, 7, 6, 6, 5, 5, 5, 5], dtype=float)
        weights /= weights.sum()
        carriers = ["AA", "DL", "UA", "WN", "AS", "B6", "NK", "F9"]
        carrier_weights = np.array([0.18, 0.17, 0.16, 0.19, 0.06, 0.07, 0.09, 0.08])
        carrier_weights /= carrier_weights.sum()
        start = pd.Timestamp("2024-01-01")
        day = rng.integers(0, 120, size=n)
        hour = rng.choice(np.arange(6, 23), size=n, p=_hour_profile())
        minute = rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55], size=n)
        carrier = rng.choice(carriers, size=n, p=carrier_weights)
        origin = rng.choice(airports, size=n, p=weights)
        dest = rng.choice(airports, size=n, p=weights)
        same = origin == dest
        dest[same] = rng.choice([a for a in airports if a != origin[0]], size=int(same.sum()))
        flight_date = start + pd.to_timedelta(day, unit="D")
        sched_dep = flight_date + pd.to_timedelta(hour * 60 + minute, unit="m")
        distance = rng.integers(180, 2600, size=n)
        crs_elapsed = np.maximum(35, distance / 7.5 + rng.normal(0, 8, size=n))
        sched_arr = sched_dep + pd.to_timedelta(crs_elapsed, unit="m")
        tail = [f"N{10000 + i % 4000:05d}" for i in range(n)]
        frame = pd.DataFrame(
            {
                "flight_date": flight_date.normalize(),
                "month": flight_date.month.astype("int16"),
                "day_of_week": (flight_date.day_of_week + 1).astype("int8"),
                "day_of_year": flight_date.day_of_year.astype("int16"),
                "carrier": carrier,
                "tail_number": tail,
                "flight_number": rng.integers(100, 3999, size=n).astype("int32"),
                "origin": origin,
                "dest": dest,
                "origin_state": "",
                "dest_state": "",
                "sched_dep_ts": sched_dep,
                "sched_arr_ts": sched_arr,
                "actual_dep_ts": pd.NaT,
                "actual_arr_ts": pd.NaT,
                "dep_hour": hour.astype("int8"),
                "dep_minute": minute.astype("int8"),
                "distance_mi": distance.astype("float32"),
                "crs_elapsed_min": crs_elapsed.astype("float32"),
                "cancelled": 0,
                "diverted": 0,
            }
        )
        # Plausible delay process: peak hours and winter weather increase risk.
        hour_factor = np.where((hour >= 15) & (hour <= 20), 0.11, 0.05)
        winter = frame["month"].isin([1, 2, 12]).to_numpy() * 0.04
        carrier_factor = pd.Series(carrier).map(
            {"AA": 0.01, "DL": 0.0, "UA": 0.01, "WN": 0.02, "AS": 0.01, "B6": 0.03, "NK": 0.04, "F9": 0.05}
        ).fillna(0.02).to_numpy()
        p_late = np.clip(hour_factor + winter + carrier_factor, 0.02, 0.45)
        late = rng.random(n) < p_late
        delay = np.where(
            late,
            rng.lognormal(mean=3.0, sigma=0.65, size=n),
            rng.normal(0, 6, size=n),
        ).clip(-20, 360)
        frame["dep_delay_min"] = delay
        frame["dep_del15"] = (delay >= 15).astype("int8")
        frame["arr_delay_min"] = delay + rng.normal(0, 8, size=n)
        frame["arr_del15"] = (frame["arr_delay_min"] >= 15).astype("int8")
        frame["actual_dep_ts"] = frame["sched_dep_ts"] + pd.to_timedelta(delay, unit="m")
        frame["actual_arr_ts"] = frame["sched_arr_ts"] + pd.to_timedelta(frame["arr_delay_min"], unit="m")
        frame["actual_elapsed_min"] = crs_elapsed + rng.normal(0, 6, size=n)
        frame["air_time_min"] = crs_elapsed * 0.82
        frame["taxi_out_min"] = rng.gamma(2, 6, size=n)
        frame["taxi_in_min"] = rng.gamma(2, 5, size=n)
        for cause in ("carrier", "weather", "nas", "security", "late_aircraft"):
            frame[f"{cause}_delay_min"] = 0.0
        frame = enrich_aircraft(frame)
        frame["is_holiday"] = is_holiday(frame["flight_date"]).astype("int8")
        frame["is_weekend"] = frame["day_of_week"].isin([6, 7]).astype("int8")
        frame["route"] = frame["origin"].astype(str) + "_" + frame["dest"].astype(str)
        frame["sched_turn_min"] = np.nan
        frame["prev_dep_delay_min"] = np.nan
        frame["prev_arr_delay_obs"] = np.nan
        frame["inbound_known"] = 0
        frame["prev_cancelled"] = -1
        frame["origin_hour_departures"] = n // (120 * 17)
        frame["origin_daily_departures"] = n // 120
        frame["route_daily_frequency"] = 2
        frame["dep_del15"] = frame["dep_del15"].astype("Int8")
        frame["arr_del15"] = frame["arr_del15"].astype("Int8")
        validate_otp(frame)
        return frame


def _hour_profile() -> np.ndarray:
    hours = np.arange(6, 23)
    weights = np.array([2, 4, 7, 9, 9, 8, 8, 8, 9, 9, 8, 7, 6, 5, 4, 3, 2], dtype=float)
    return weights / weights.sum()


def build_otp_dataset(cfg: Config, force: bool = False) -> Path:
    """Build (or reuse) the canonical OTP parquet."""
    destination = cfg.path("data.otp.parquet")
    if destination.exists() and not force:
        logger.info("OTP parquet already exists (%s); use force=True to rebuild", destination)
        return destination
    source = str(cfg.get("data.otp.source", "bts")).lower()
    provider: OTPDataProvider = BTSOTPProvider() if source == "bts" else SyntheticOTPProvider()
    logger.info("Building OTP dataset from provider=%s", provider.name)
    frame = provider.load(cfg) if source == "synthetic" else load_bts_frame(cfg)
    write_parquet(frame, destination, compression="zstd")
    logger.info("Wrote %s rows to %s", len(frame), destination)
    return destination


def load_otp(cfg: Config) -> pd.DataFrame:
    path = cfg.path("data.otp.parquet")
    if not path.exists():
        build_otp_dataset(cfg)
    return pd.read_parquet(path)
