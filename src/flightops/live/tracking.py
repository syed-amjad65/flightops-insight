"""Live aircraft tracking through the OpenSky Network public ADS-B API.

The public ``/api/states/all`` endpoint returns one *state vector* per aircraft:

    icao24, callsign, origin_country, time_position, last_contact,
    longitude, latitude, baro_altitude, on_ground, velocity, true_track,
    vertical_rate, sensors, geo_altitude, squawk, spi, position_source,
    category (optional)

OpenSky uses SI units: altitude in metres, velocity in m/s and vertical rate
in m/s.  This module converts everything into the aviation units the rest of
the project and the app use (feet, knots, feet per minute) and adds a simple
flight phase.

Anonymous access is heavily rate limited.  Set ``OPENSKY_ACCESS_TOKEN`` or
``OPENSKY_CLIENT_ID``/``OPENSKY_CLIENT_SECRET`` in the environment to use
OAuth2 and raise the limit.  If the API is unavailable the module can return a
clearly-labelled deterministic demo snapshot so the page still renders.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import requests

from flightops.config import Config
from flightops.logging_utils import get_logger

logger = get_logger("live.tracking")

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)

M_TO_FT = 3.280839895
MS_TO_KT = 1.9438444924406
MS_TO_FPM = 196.8503937

# Raw OpenSky state vector fields, in API order.  Some deployments omit the
# trailing ``category`` field, so parsing pads short vectors with ``None``.
STATE_COLUMNS: tuple[str, ...] = (
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude_m",
    "on_ground",
    "velocity_ms",
    "true_track_deg",
    "vertical_rate_ms",
    "sensors",
    "geo_altitude_m",
    "squawk",
    "spi",
    "position_source",
    "category",
)

NUMERIC_COLUMNS: tuple[str, ...] = (
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude_m",
    "velocity_ms",
    "true_track_deg",
    "vertical_rate_ms",
    "geo_altitude_m",
    "category",
)

DERIVED_COLUMNS: tuple[str, ...] = (
    "altitude_ft",
    "geo_altitude_ft",
    "speed_kt",
    "vertical_rate_fpm",
    "flight",
    "phase",
    "last_contact_utc",
    "position_age_s",
    "is_demo",
)


class TrackingError(RuntimeError):
    """Raised when a live tracking request fails or returns no usable data."""


@dataclass(frozen=True)
class Region:
    """A rectangular geographic query window for the OpenSky API."""

    name: str
    lamin: float
    lomin: float
    lamax: float
    lomax: float

    def params(self) -> dict[str, float]:
        return {
            "lamin": float(self.lamin),
            "lomin": float(self.lomin),
            "lamax": float(self.lamax),
            "lomax": float(self.lomax),
        }

    @property
    def is_global(self) -> bool:
        return (
            self.lamin <= -90.0
            and self.lamax >= 90.0
            and self.lomin <= -180.0
            and self.lomax >= 180.0
        )

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.params()}


DEFAULT_REGIONS: dict[str, Region] = {
    "India": Region("India", 6.0, 68.0, 36.0, 98.0),
    "United States": Region("United States", 24.0, -125.0, 50.0, -66.0),
    "Europe": Region("Europe", 35.0, -12.0, 72.0, 45.0),
    "Global": Region("Global", -90.0, -180.0, 90.0, 180.0),
}


def region_catalog(cfg: Config | None = None) -> dict[str, Region]:
    """Return the configured regions, falling back to the built-in defaults."""
    catalog = dict(DEFAULT_REGIONS)
    if cfg is None:
        return catalog
    raw = cfg.get("live_tracking.regions") or {}
    for name, bounds in raw.items():
        if not isinstance(bounds, Mapping):
            continue
        try:
            catalog[str(name)] = Region(
                str(name),
                float(bounds["lamin"]),
                float(bounds["lomin"]),
                float(bounds["lamax"]),
                float(bounds["lomax"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring malformed live_tracking region %s: %s", name, bounds)
    return catalog


def get_region(
    name: str,
    custom: Region | None = None,
    catalog: Mapping[str, Region] | None = None,
) -> Region:
    """Resolve a region name; ``Custom`` uses the supplied bounds."""
    if name.lower() == "custom":
        if custom is None:
            raise TrackingError("Custom region selected but no bounds were supplied.")
        return custom
    source = catalog or DEFAULT_REGIONS
    if name not in source:
        raise TrackingError(f"Unknown region '{name}'. Available: {', '.join(source)}")
    return source[name]


def access_token_from_env() -> str | None:
    """Return an OAuth token from the environment, if the operator set one."""
    token = os.environ.get("OPENSKY_ACCESS_TOKEN")
    return token.strip() or None if token else None


def fetch_client_credentials_token(
    client_id: str,
    client_secret: str,
    timeout: float = 20.0,
) -> str:
    """Exchange OpenSky OAuth2 client credentials for an access token."""
    try:
        response = requests.post(
            OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
    except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network
        raise TrackingError(f"OpenSky OAuth token request failed: {exc}") from exc
    if not token:
        raise TrackingError("OpenSky OAuth response did not contain an access_token.")
    return str(token)


_TOKEN_CACHE: dict[str, Any] = {"token": None, "client_id": None, "expires_at": 0.0}
_TOKEN_CACHE_SECONDS = 20 * 60


def resolve_access_token(timeout: float = 20.0) -> str | None:
    """Resolve an OpenSky token from the environment.

    Priority: an explicit ``OPENSKY_ACCESS_TOKEN``, then OAuth2 client
    credentials (cached in-process), otherwise anonymous access.
    """
    token = access_token_from_env()
    if token:
        return token

    client_id = os.environ.get("OPENSKY_CLIENT_ID")
    client_secret = os.environ.get("OPENSKY_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None

    now = time.monotonic()
    if (
        _TOKEN_CACHE["token"]
        and _TOKEN_CACHE["client_id"] == client_id
        and _TOKEN_CACHE["expires_at"] > now
    ):
        return str(_TOKEN_CACHE["token"])

    token = fetch_client_credentials_token(client_id, client_secret, timeout=timeout)
    _TOKEN_CACHE.update(
        {
            "token": token,
            "client_id": client_id,
            "expires_at": now + _TOKEN_CACHE_SECONDS,
        }
    )
    return token


def fetch_states(
    region: Region,
    timeout: float = 20.0,
    token: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch the current state vectors for a region from OpenSky."""
    params = None if region.is_global else region.params()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    requester = session or requests
    try:
        response = requester.get(
            OPENSKY_STATES_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise TrackingError(f"OpenSky request failed: {exc}") from exc
    except ValueError as exc:
        raise TrackingError(f"OpenSky returned a non-JSON response: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise TrackingError("OpenSky returned an unexpected payload.")
    return dict(payload)


def _pad_state(state: Sequence[Any], length: int) -> list[Any]:
    row = list(state)[:length]
    return row + [None] * (length - len(row))


def empty_states() -> pd.DataFrame:
    """An empty frame with the full canonical + derived schema."""
    columns = list(STATE_COLUMNS) + list(DERIVED_COLUMNS)
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def _add_phase(frame: pd.DataFrame) -> pd.Series:
    on_ground = frame["on_ground"].fillna(False).astype(bool)
    vertical = frame["vertical_rate_fpm"]
    phase = np.where(
        on_ground,
        "On ground",
        np.where(
            vertical > 300,
            "Climbing",
            np.where(vertical < -300, "Descending", "Level"),
        ),
    )
    return pd.Series(phase, index=frame.index, dtype="object")


def parse_states(payload: Mapping[str, Any], now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Convert an OpenSky payload into a typed, enriched aircraft frame."""
    raw_states = payload.get("states") if isinstance(payload, Mapping) else None
    if not raw_states:
        return empty_states()

    rows = [_pad_state(state, len(STATE_COLUMNS)) for state in raw_states if state]
    frame = pd.DataFrame(rows, columns=list(STATE_COLUMNS))

    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["icao24"] = frame["icao24"].astype("string").str.strip().str.lower()
    frame["callsign"] = frame["callsign"].astype("string").str.strip()
    frame["origin_country"] = frame["origin_country"].astype("string").str.strip()
    frame["squawk"] = frame["squawk"].astype("string").str.strip()
    frame["position_source"] = frame["position_source"].astype("string").str.strip()

    frame["on_ground"] = frame["on_ground"].fillna(False).astype(bool)
    frame["spi"] = frame["spi"].fillna(False).astype(bool)

    # Drop states without a usable position; they cannot be mapped.
    frame = frame.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    if frame.empty:
        return empty_states()

    frame["latitude"] = frame["latitude"].astype(float)
    frame["longitude"] = frame["longitude"].astype(float)

    frame["altitude_ft"] = frame["baro_altitude_m"] * M_TO_FT
    frame["geo_altitude_ft"] = frame["geo_altitude_m"] * M_TO_FT
    frame["speed_kt"] = frame["velocity_ms"] * MS_TO_KT
    frame["vertical_rate_fpm"] = frame["vertical_rate_ms"] * MS_TO_FPM

    frame["flight"] = frame["callsign"].where(
        frame["callsign"].fillna("").astype(str).str.len() > 0,
        frame["icao24"],
    )
    frame["phase"] = _add_phase(frame)

    now = now or pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    frame["last_contact_utc"] = pd.to_datetime(
        frame["last_contact"], unit="s", utc=True, errors="coerce"
    )
    frame["position_age_s"] = (now - frame["last_contact_utc"]).dt.total_seconds()
    frame["is_demo"] = False

    frame = frame.sort_values(
        by=["on_ground", "altitude_ft"], ascending=[True, False], na_position="last"
    ).reset_index(drop=True)
    return frame


def demo_states(
    region: Region,
    n: int = 60,
    seed: int = 42,
) -> pd.DataFrame:
    """Build a deterministic demo snapshot inside a region's bounds.

    This is used only when OpenSky is unreachable and the operator allows the
    demo fallback.  Every row is flagged with ``is_demo = True`` so it can be
    labelled unambiguously in the UI.
    """
    rng = np.random.default_rng(seed)
    n = max(int(n), 1)
    prefixes = ["AIC", "IGO", "SEJ", "VTI", "BAW", "DLH", "UAE", "SIA", "QTR", "KLM"]
    countries = [
        "India",
        "United States",
        "Germany",
        "United Arab Emirates",
        "Singapore",
        "United Kingdom",
        "Netherlands",
        "Qatar",
    ]
    now = int(pd.Timestamp.now(tz="UTC").timestamp())

    latitudes = rng.uniform(region.lamin, region.lamax, n)
    longitudes = rng.uniform(region.lomin, region.lomax, n)
    on_ground = rng.random(n) < 0.18
    altitude_m = np.where(on_ground, rng.uniform(0, 5, n), rng.uniform(1800, 12500, n))
    velocity_ms = np.where(on_ground, rng.uniform(0, 12, n), rng.uniform(90, 280, n))
    vertical_ms = np.where(on_ground, 0.0, rng.normal(0, 4.0, n))

    records: list[list[Any]] = []
    for index in range(n):
        callsign = f"{prefixes[index % len(prefixes)]}{100 + index:03d}"
        records.append(
            [
                f"{rng.integers(0, 0xFFFFFF):06x}",
                callsign,
                countries[index % len(countries)],
                now - int(rng.integers(0, 15)),
                now - int(rng.integers(0, 25)),
                float(longitudes[index]),
                float(latitudes[index]),
                float(altitude_m[index]),
                bool(on_ground[index]),
                float(velocity_ms[index]),
                float(rng.uniform(0, 360)),
                float(vertical_ms[index]),
                None,
                float(altitude_m[index] + rng.uniform(-30, 30)),
                f"{int(rng.integers(1000, 7777)):04d}",
                False,
                0,
                0,
            ]
        )

    frame = parse_states({"time": now, "states": records})
    frame["is_demo"] = True
    return frame


def filter_states(
    frame: pd.DataFrame,
    countries: Iterable[str] | None = None,
    on_ground: bool | None = None,
    min_altitude_ft: float | None = None,
    max_altitude_ft: float | None = None,
    search: str | None = None,
) -> pd.DataFrame:
    """Apply the app's live-traffic filters to a parsed frame."""
    if frame.empty:
        return frame.copy()
    out = frame
    if countries:
        wanted = {str(country) for country in countries}
        out = out[out["origin_country"].isin(wanted)]
    if on_ground is not None:
        out = out[out["on_ground"] == bool(on_ground)]
    altitude = out["altitude_ft"].fillna(0.0)
    if min_altitude_ft is not None:
        out = out[altitude >= float(min_altitude_ft)]
        altitude = out["altitude_ft"].fillna(0.0)
    if max_altitude_ft is not None:
        out = out[altitude <= float(max_altitude_ft)]
    if search:
        needle = str(search).strip().lower()
        if needle:
            callsign = out["flight"].fillna("").astype(str).str.lower()
            icao = out["icao24"].fillna("").astype(str).str.lower()
            out = out[callsign.str.contains(needle, regex=False) | icao.str.contains(needle, regex=False)]
    return out.reset_index(drop=True)


def summarise_states(frame: pd.DataFrame) -> dict[str, float | int]:
    """Headline KPIs for a live snapshot."""
    if frame.empty:
        return {
            "tracked": 0,
            "airborne": 0,
            "on_ground": 0,
            "countries": 0,
            "avg_altitude_ft": 0.0,
            "max_altitude_ft": 0.0,
            "avg_speed_kt": 0.0,
            "climbing": 0,
            "descending": 0,
        }
    airborne = frame[~frame["on_ground"].fillna(False).astype(bool)]
    vertical = airborne["vertical_rate_fpm"]
    return {
        "tracked": int(len(frame)),
        "airborne": int(len(airborne)),
        "on_ground": int(frame["on_ground"].fillna(False).astype(bool).sum()),
        "countries": int(frame["origin_country"].nunique(dropna=True)),
        "avg_altitude_ft": float(airborne["altitude_ft"].mean()) if not airborne.empty else 0.0,
        "max_altitude_ft": float(airborne["altitude_ft"].max()) if not airborne.empty else 0.0,
        "avg_speed_kt": float(airborne["speed_kt"].mean()) if not airborne.empty else 0.0,
        "climbing": int((vertical > 300).sum()),
        "descending": int((vertical < -300).sum()),
    }


def load_tracking(
    region: Region,
    timeout: float = 20.0,
    token: str | None = None,
    allow_demo: bool = True,
    seed: int = 42,
    demo_count: int = 60,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a snapshot, with an explicit, labelled demo fallback.

    Returns ``(frame, metadata)``.  ``metadata`` always records the source,
    the fetch time, the region and any error, so the app can be honest about
    what the operator is looking at.
    """
    fetched_at = pd.Timestamp.now(tz="UTC")
    meta: dict[str, Any] = {
        "source": "OpenSky Network",
        "fetched_at": fetched_at.isoformat(),
        "region": region.as_dict(),
        "error": None,
    }
    try:
        payload = fetch_states(region, timeout=timeout, token=token)
        frame = parse_states(payload, now=fetched_at)
        if frame.empty:
            raise TrackingError("OpenSky returned no aircraft with a position in this region.")
        meta["provider_time"] = payload.get("time")
        return frame, meta
    except TrackingError as exc:
        logger.warning("Live tracking unavailable: %s", exc)
        meta["error"] = str(exc)
        if not allow_demo:
            return empty_states(), meta
        frame = demo_states(region, n=demo_count, seed=seed)
        meta["source"] = "Demo fallback"
        return frame, meta
