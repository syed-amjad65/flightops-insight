"""Project-wide logging helpers with a console and rotating file handler."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flightops.config import PROJECT_ROOT

_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir = PROJECT_ROOT / "reports" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("flightops")
    root.setLevel(logging.INFO)
    root.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "flightops.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger; safe to call repeatedly."""
    _configure_root()
    return logging.getLogger(f"flightops.{name}" if not name.startswith("flightops") else name)


def log_section(logger: logging.Logger, title: str) -> None:
    """Visually separate pipeline stages in the console and log file."""
    logger.info("=" * 78)
    logger.info(title)
    logger.info("=" * 78)
