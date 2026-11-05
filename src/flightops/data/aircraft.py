"""Reference/enrichment data for the OTP module.

Important provenance note
-------------------------
BTS On-Time Performance contains a tail number but **not** an aircraft type.
The public FAA registry was unavailable from this environment (HTTP 503).
Aircraft family/type is therefore a *documented synthetic enrichment*:
a deterministic hash of the tail number selects a type from the operating
carrier's plausible 2024 fleet mix.  It is stable across months and is clearly
labelled as synthetic in the model card and limitations.

In production this module is replaced by an aircraft registry/Skywise feed
keyed on tail number; downstream feature code does not change.
"""

from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd

# Approximate 2024 US passenger-carrier fleet mixes. Weights are planning
# assumptions, not an official fleet census.
FLEET_MIX: dict[str, list[tuple[str, float]]] = {
    "AA": [("A321", 0.30), ("B738", 0.28), ("A320", 0.14), ("B77W", 0.04), ("B788", 0.04), ("E175", 0.10), ("A319", 0.10)],
    "DL": [("B739", 0.28), ("A321", 0.18), ("A320", 0.14), ("B752", 0.08), ("B717", 0.10), ("A333", 0.04), ("B763", 0.04), ("A220", 0.08), ("E175", 0.06)],
    "UA": [("B739", 0.28), ("B738", 0.18), ("A320", 0.14), ("B752", 0.08), ("B772", 0.04), ("B789", 0.04), ("E175", 0.12), ("A319", 0.06), ("B763", 0.06)],
    "WN": [("B738", 0.55), ("B737", 0.45)],
    "AS": [("B739", 0.36), ("B738", 0.30), ("E175", 0.34)],
    "B6": [("A320", 0.48), ("A321", 0.32), ("A220", 0.20)],
    "NK": [("A320", 0.58), ("A321", 0.42)],
    "F9": [("A320", 0.50), ("A321", 0.45), ("A319", 0.05)],
    "G4": [("A320", 0.55), ("A319", 0.45)],
    "HA": [("B712", 0.30), ("A321", 0.40), ("A332", 0.20), ("B789", 0.10)],
}
DEFAULT_MIX = [("B738", 0.40), ("A320", 0.35), ("E175", 0.25)]

TYPE_ATTRIBUTES: dict[str, tuple[str, int, int]] = {
    # type: (family, seats, minimum turnaround minutes)
    "A319": ("narrowbody", 128, 40),
    "A320": ("narrowbody", 160, 45),
    "A321": ("narrowbody", 190, 50),
    "A220": ("narrowbody", 130, 40),
    "B737": ("narrowbody", 143, 40),
    "B738": ("narrowbody", 162, 45),
    "B739": ("narrowbody", 178, 48),
    "B717": ("narrowbody", 110, 35),
    "B752": ("narrowbody", 200, 55),
    "B763": ("widebody", 245, 70),
    "B772": ("widebody", 310, 80),
    "B77W": ("widebody", 350, 85),
    "B788": ("widebody", 250, 75),
    "B789": ("widebody", 290, 80),
    "A332": ("widebody", 260, 75),
    "A333": ("widebody", 300, 80),
    "E175": ("regional", 76, 30),
}

HUBS: dict[str, set[str]] = {
    "AA": {"DFW", "CLT", "ORD", "PHX", "MIA", "LAX", "DCA"},
    "DL": {"ATL", "DTW", "MSP", "JFK", "LAX", "SEA", "SLC"},
    "UA": {"ORD", "DEN", "IAH", "EWR", "SFO", "LAX", "IAD"},
    "WN": {"DAL", "HOU", "PHX", "BWI", "DEN", "LAS", "MDW"},
    "AS": {"SEA", "PDX", "SFO", "LAX", "ANC"},
    "B6": {"JFK", "BOS", "FLL", "MCO"},
    "NK": {"FLL", "MCO", "LAS", "DFW"},
    "F9": {"DEN", "LAS", "MCO"},
    "G4": {"LAS", "SFB", "PIE"},
    "HA": {"HNL", "OGG", "LAX"},
}

# 2024 US holiday travel windows (inclusive). Used as a planning proxy only.
HOLIDAY_WINDOWS_2024: tuple[tuple[date, date], ...] = (
    (date(2024, 1, 1), date(2024, 1, 2)),
    (date(2024, 1, 13), date(2024, 1, 16)),
    (date(2024, 2, 17), date(2024, 2, 20)),
    (date(2024, 5, 24), date(2024, 5, 28)),
    (date(2024, 7, 3), date(2024, 7, 8)),
    (date(2024, 8, 30), date(2024, 9, 3)),
    (date(2024, 11, 22), date(2024, 12, 2)),
    (date(2024, 12, 20), date(2024, 12, 31)),
)


def _stable_uniform(key: str) -> float:
    """Deterministic uniform [0, 1) from a string, independent of process seed."""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def assign_aircraft_type(tail_number: str | None, carrier: str | None) -> str:
    """Return a deterministic aircraft type for a (tail, carrier) pair."""
    if not isinstance(tail_number, str) or not tail_number.strip():
        # Use a stable carrier-level pseudo-tail so missing tails still map.
        tail_number = f"__NO_TAIL__{carrier or 'UNK'}"
    mix = FLEET_MIX.get(str(carrier).upper(), DEFAULT_MIX)
    draw = _stable_uniform(f"{tail_number}|{carrier}")
    cumulative = 0.0
    for aircraft_type, weight in mix:
        cumulative += weight
        if draw <= cumulative:
            return aircraft_type
    return mix[-1][0]


def type_attributes(aircraft_type: str) -> tuple[str, int, int]:
    return TYPE_ATTRIBUTES.get(aircraft_type, ("narrowbody", 160, 45))


def enrich_aircraft(frame: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic aircraft attributes and hub flags.

    The input must contain ``tail_number``, ``carrier``, ``origin`` and ``dest``.
    """
    result = frame.copy()
    unique = (
        result[["tail_number", "carrier"]]
        .drop_duplicates()
        .dropna(subset=["carrier"])
        .copy()
    )
    unique["aircraft_type"] = [
        assign_aircraft_type(tail, carrier)
        for tail, carrier in zip(unique["tail_number"], unique["carrier"])
    ]
    unique["aircraft_family"] = [type_attributes(t)[0] for t in unique["aircraft_type"]]
    unique["seats"] = [type_attributes(t)[1] for t in unique["aircraft_type"]]
    unique["min_turn_min"] = [type_attributes(t)[2] for t in unique["aircraft_type"]]

    result = result.merge(unique, on=["tail_number", "carrier"], how="left")
    fallback_type = "B738"
    result["aircraft_type"] = result["aircraft_type"].fillna(fallback_type)
    result["aircraft_family"] = result["aircraft_family"].fillna("narrowbody")
    result["seats"] = result["seats"].fillna(160).astype("int16")
    result["min_turn_min"] = result["min_turn_min"].fillna(45).astype("int16")

    hub_pairs = {(carrier, airport) for carrier, airports in HUBS.items() for airport in airports}
    result["is_hub_origin"] = [
        int((carrier, airport) in hub_pairs)
        for carrier, airport in zip(result["carrier"], result["origin"])
    ]
    result["is_hub_dest"] = [
        int((carrier, airport) in hub_pairs)
        for carrier, airport in zip(result["carrier"], result["dest"])
    ]
    return result


def is_holiday(flight_date: pd.Series) -> pd.Series:
    """Return 1 inside a 2024 holiday travel window, else 0."""
    dates = pd.to_datetime(flight_date).dt.date
    flags = pd.Series(0, index=flight_date.index, dtype="int8")
    for start, end in HOLIDAY_WINDOWS_2024:
        flags[(dates >= start) & (dates <= end)] = 1
    return flags
