"""Tests for OpenSky state parsing, filtering, summaries and fallbacks."""

from __future__ import annotations

import pandas as pd
import pytest

from flightops.config import load_config
from flightops.live import tracking
from flightops.live.tracking import (
    Region,
    TrackingError,
    demo_states,
    empty_states,
    filter_states,
    get_region,
    load_tracking,
    parse_states,
    region_catalog,
    summarise_states,
)


def _state(
    icao24: str = "abc123",
    callsign: str = "AIC101 ",
    country: str = "India",
    lat: float = 22.0,
    lon: float = 78.0,
    baro_altitude_m: float | None = 10000.0,
    on_ground: bool = False,
    velocity_ms: float | None = 250.0,
    vertical_rate_ms: float | None = 2.0,
    squawk: str | None = "1234",
) -> list:
    """A full 18-field OpenSky state vector."""
    return [
        icao24,
        callsign,
        country,
        1_700_000_000,
        1_700_000_000,
        lon,
        lat,
        baro_altitude_m,
        on_ground,
        velocity_ms,
        90.0,
        vertical_rate_ms,
        None,
        10_010.0,
        squawk,
        False,
        0,
        0,
    ]


def _payload(*states: list) -> dict:
    return {"time": 1_700_000_000, "states": list(states)}


def test_parse_states_converts_units_and_assigns_phase() -> None:
    payload = _payload(
        _state(callsign="AIC101 ", baro_altitude_m=10_000.0, velocity_ms=250.0, vertical_rate_ms=2.0),
        _state(
            icao24="def456",
            callsign="",
            lat=28.6,
            lon=77.2,
            baro_altitude_m=0.0,
            on_ground=True,
            velocity_ms=0.0,
            vertical_rate_ms=0.0,
        ),
        _state(
            icao24="ghi789",
            callsign="IGO55",
            lat=19.1,
            lon=72.9,
            baro_altitude_m=9000.0,
            vertical_rate_ms=-6.0,
        ),
    )

    frame = parse_states(payload)

    assert len(frame) == 3
    callsigns = frame.set_index("icao24")["callsign"].to_dict()
    assert callsigns["abc123"] == "AIC101"
    assert callsigns["def456"] == ""
    assert callsigns["ghi789"] == "IGO55"
    # Empty callsigns fall back to ICAO24 so every row has a label.
    assert frame.loc[frame["icao24"] == "def456", "flight"].item() == "def456"
    assert frame.loc[frame["icao24"] == "abc123", "altitude_ft"].item() == pytest.approx(32_808.4, rel=1e-4)
    assert frame.loc[frame["icao24"] == "abc123", "speed_kt"].item() == pytest.approx(485.96, rel=1e-3)
    assert frame.loc[frame["icao24"] == "abc123", "vertical_rate_fpm"].item() == pytest.approx(393.7, rel=1e-3)
    phases = frame.set_index("icao24")["phase"].to_dict()
    assert phases["abc123"] == "Climbing"
    assert phases["def456"] == "On ground"
    assert phases["ghi789"] == "Descending"
    assert frame["is_demo"].eq(False).all()


def test_parse_states_drops_rows_without_position_and_handles_short_vectors() -> None:
    short = ["abc123", "SHORT1", "India", 1, 2, None, None]
    valid = _state(icao24="valid1")
    frame = parse_states(_payload(short, valid))
    assert frame["icao24"].tolist() == ["valid1"]


def test_parse_states_empty_payload_returns_empty_schema() -> None:
    frame = parse_states({"time": 1, "states": None})
    assert frame.empty
    assert "altitude_ft" in frame.columns
    # An explicitly empty frame keeps the same schema as a parsed one.
    assert list(frame.columns) == list(empty_states().columns)


def test_filter_states_combines_country_ground_altitude_and_search() -> None:
    frame = parse_states(
        _payload(
            _state(icao24="aaa111", callsign="AIC101", country="India", baro_altitude_m=10_000),
            _state(icao24="bbb222", callsign="BAW22", country="United Kingdom", baro_altitude_m=9_500),
            _state(
                icao24="ccc333",
                callsign="IGO77",
                country="India",
                baro_altitude_m=0.0,
                on_ground=True,
            ),
        )
    )

    airborne_india = filter_states(frame, countries=["India"], on_ground=False)
    assert airborne_india["icao24"].tolist() == ["aaa111"]

    in_band = filter_states(frame, min_altitude_ft=30_000, max_altitude_ft=40_000)
    assert set(in_band["icao24"]) == {"aaa111", "bbb222"}

    by_search = filter_states(frame, search="igo")
    assert by_search["icao24"].tolist() == ["ccc333"]

    by_icao = filter_states(frame, search="BBB")
    assert by_icao["icao24"].tolist() == ["bbb222"]


def test_summarise_states_counts_and_averages() -> None:
    frame = parse_states(
        _payload(
            _state(icao24="aaa111", country="India", baro_altitude_m=10_000, velocity_ms=250),
            _state(icao24="bbb222", country="India", baro_altitude_m=8_000, velocity_ms=200),
            _state(
                icao24="ccc333",
                country="Qatar",
                baro_altitude_m=0.0,
                on_ground=True,
                velocity_ms=0.0,
                vertical_rate_ms=0.0,
            ),
        )
    )
    summary = summarise_states(frame)
    assert summary["tracked"] == 3
    assert summary["airborne"] == 2
    assert summary["on_ground"] == 1
    assert summary["countries"] == 2
    assert summary["avg_altitude_ft"] > 0
    assert summary["climbing"] == 2
    assert summary["descending"] == 0


def test_summarise_states_on_empty_frame_is_all_zero() -> None:
    summary = summarise_states(empty_states())
    assert summary["tracked"] == 0
    assert summary["avg_altitude_ft"] == 0.0


def test_demo_states_are_deterministic_flagged_and_inside_bounds() -> None:
    region = Region("Test", 10.0, 70.0, 20.0, 80.0)
    first = demo_states(region, n=25, seed=7)
    second = demo_states(region, n=25, seed=7)
    assert len(first) == 25
    assert first["is_demo"].all()
    assert first["latitude"].between(10.0, 20.0).all()
    assert first["longitude"].between(70.0, 80.0).all()
    assert first["flight"].tolist() == second["flight"].tolist()
    assert first["latitude"].tolist() == second["latitude"].tolist()


def test_get_region_and_catalog() -> None:
    cfg = load_config()
    catalog = region_catalog(cfg)
    assert "India" in catalog
    assert "Global" in catalog
    assert catalog["India"].lamin == 6.0

    custom = Region("Custom", -10.0, 20.0, 10.0, 40.0)
    assert get_region("Custom", custom=custom) is custom
    with pytest.raises(TrackingError):
        get_region("Custom")
    with pytest.raises(TrackingError):
        get_region("Atlantis")


def test_region_catalog_accepts_config_overrides() -> None:
    cfg = load_config().with_overrides(
        {"live_tracking": {"regions": {"Testland": {"lamin": 1, "lomin": 2, "lamax": 3, "lomax": 4}}}}
    )
    catalog = region_catalog(cfg)
    assert catalog["Testland"] == Region("Testland", 1.0, 2.0, 3.0, 4.0)
    # Built-in regions are still available after a partial override.
    assert "India" in catalog


def test_load_tracking_falls_back_to_labelled_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args, **kwargs):
        raise TrackingError("simulated outage")

    monkeypatch.setattr(tracking, "fetch_states", _fail)
    region = Region("Test", 10.0, 70.0, 20.0, 80.0)
    frame, meta = load_tracking(region, allow_demo=True)
    assert len(frame) > 0
    assert frame["is_demo"].all()
    assert meta["source"] == "Demo fallback"
    assert "simulated outage" in meta["error"]


def test_load_tracking_returns_empty_when_demo_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args, **kwargs):
        raise TrackingError("simulated outage")

    monkeypatch.setattr(tracking, "fetch_states", _fail)
    region = Region("Test", 10.0, 70.0, 20.0, 80.0)
    frame, meta = load_tracking(region, allow_demo=False)
    assert frame.empty
    assert meta["source"] == "OpenSky Network"
    assert "simulated outage" in meta["error"]


def test_resolve_access_token_priority_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSKY_ACCESS_TOKEN", "explicit")
    assert tracking.resolve_access_token() == "explicit"

    monkeypatch.delenv("OPENSKY_ACCESS_TOKEN")
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "client-id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "client-secret")
    tracking._TOKEN_CACHE.update({"token": None, "client_id": None, "expires_at": 0.0})
    calls: list[int] = []

    def _issue(*args, **kwargs):
        calls.append(1)
        return "issued-token"

    monkeypatch.setattr(tracking, "fetch_client_credentials_token", _issue)
    assert tracking.resolve_access_token() == "issued-token"
    assert tracking.resolve_access_token() == "issued-token"
    assert len(calls) == 1


def test_load_tracking_uses_live_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tracking,
        "fetch_states",
        lambda *args, **kwargs: _payload(_state(icao24="live1", callsign="LIVE1")),
    )
    region = Region("Test", 10.0, 70.0, 20.0, 80.0)
    frame, meta = load_tracking(region)
    assert meta["source"] == "OpenSky Network"
    assert meta["error"] is None
    assert frame["icao24"].tolist() == ["live1"]
    assert not frame["is_demo"].any()
