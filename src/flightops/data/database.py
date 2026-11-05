"""DuckDB query layer: build analytics tables from the canonical parquet."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

from flightops.config import Config
from flightops.logging_utils import get_logger

logger = get_logger("data.database")


def _split_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def build_database(cfg: Config, force: bool = False) -> Path:
    """Create/replace the local DuckDB analytics database."""
    db_path = cfg.path("data.otp.duckdb")
    parquet = cfg.path("data.otp.parquet")
    if not parquet.exists():
        raise FileNotFoundError(f"Missing OTP parquet: {parquet}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and force:
        db_path.unlink()

    schema_path = cfg.path("sql.schema_file")
    schema_sql = schema_path.read_text(encoding="utf-8")
    watchlist = cfg.path("paths.metrics") / "rul_watchlist.csv"
    maintenance = cfg.path("paths.optimization") / "maintenance_plan_optimized.csv"

    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            f"CREATE OR REPLACE VIEW flights AS SELECT * FROM read_parquet('{parquet.as_posix()}')"
        )
        for statement in _split_statements(schema_sql):
            if "rul_watchlist.csv" in statement and not watchlist.exists():
                logger.warning("Skipping fleet_watchlist view; run RUL training first")
                continue
            if "maintenance_plan_optimized.csv" in statement and not maintenance.exists():
                logger.warning("Skipping maintenance_plan view; run optimization first")
                continue
            connection.execute(statement)
        connection.execute("ANALYZE")
        logger.info("DuckDB analytics database ready: %s", db_path)
    finally:
        connection.close()
    return db_path


def connect(cfg: Config, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(cfg.path("data.otp.duckdb")), read_only=read_only)


def query_df(cfg: Config, sql: str, params: list | None = None) -> pd.DataFrame:
    """Run one query and return a pandas frame."""
    connection = connect(cfg)
    try:
        return connection.execute(sql, params or []).df()
    finally:
        connection.close()


def _load_named_queries(cfg: Config) -> dict[str, str]:
    text = cfg.path("sql.queries_file").read_text(encoding="utf-8")
    queries: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = re.match(r"--\s*name:\s*(\w+)", line)
        if match:
            if current and buffer:
                queries[current] = "\n".join(buffer).strip()
            current = match.group(1)
            buffer = []
        elif current and line.strip() and not line.strip().startswith("--"):
            buffer.append(line)
    if current and buffer:
        queries[current] = "\n".join(buffer).strip()
    return queries


def query_named(cfg: Config, name: str) -> pd.DataFrame:
    queries = _load_named_queries(cfg)
    if name not in queries:
        raise KeyError(f"Unknown named query: {name}")
    return query_df(cfg, queries[name])
