"""Live flight tracking page: OpenSky ADS-B map, filters and aircraft detail."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from flightops.app.common import (
    assumption_box,
    get_config,
    inject_css,
    page_header,
    so_what,
)
from flightops.live.tracking import (
    Region,
    filter_states,
    get_region,
    load_tracking,
    region_catalog,
    resolve_access_token,
    summarise_states,
)
from flightops.logging_utils import get_logger
from flightops.security import escape_html, escape_markdown

logger = get_logger("app.live_tracking")

PHASE_COLORS = {
    "On ground": "#6b7280",
    "Climbing": "#16a34a",
    "Descending": "#ea580c",
    "Level": "#2563eb",
}

TABLE_COLUMNS = {
    "flight": "Flight",
    "icao24": "ICAO24",
    "origin_country": "Country",
    "phase": "Phase",
    "altitude_ft": "Altitude (ft)",
    "speed_kt": "Speed (kt)",
    "vertical_rate_fpm": "Vertical rate (ft/min)",
    "true_track_deg": "Heading (°)",
    "squawk": "Squawk",
    "position_age_s": "Position age (s)",
}


@st.cache_data(ttl=30, show_spinner=False)
def _snapshot(
    region: Region,
    timeout: float,
    allow_demo: bool,
    nonce: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Cached OpenSky snapshot; ``nonce`` forces a manual refresh."""
    return load_tracking(
        region,
        timeout=timeout,
        token=resolve_access_token(),
        allow_demo=allow_demo,
    )


def _zoom_for(region: Region) -> float:
    lat_span = abs(region.lamax - region.lamin)
    if lat_span > 120:
        return 1.2
    if lat_span > 50:
        return 2.0
    if lat_span > 25:
        return 3.0
    if lat_span > 10:
        return 3.8
    return 5.0


def _region_controls(cfg) -> dict[str, Any]:
    catalog = region_catalog(cfg)
    names = list(catalog) + ["Custom"]
    default = str(cfg.get("live_tracking.default_region", "India"))
    default_index = names.index(default) if default in names else 0
    refresh_seconds = int(cfg.get("live_tracking.refresh_seconds", 60))
    allow_demo_default = bool(cfg.get("live_tracking.allow_demo_fallback", True))

    with st.sidebar:
        st.markdown("### Live traffic")
        name = st.selectbox("Region", names, index=default_index)
        custom: Region | None = None
        if name == "Custom":
            left, right = st.columns(2)
            lamin = left.number_input("Min latitude", -90.0, 90.0, 6.0, 1.0)
            lamax = right.number_input("Max latitude", -90.0, 90.0, 36.0, 1.0)
            lomin = left.number_input("Min longitude", -180.0, 180.0, 68.0, 1.0)
            lomax = right.number_input("Max longitude", -180.0, 180.0, 98.0, 1.0)
            custom = Region(
                "Custom",
                min(lamin, lamax),
                min(lomin, lomax),
                max(lamin, lamax),
                max(lomin, lomax),
            )

        allow_demo = st.toggle(
            "Demo fallback if OpenSky is unavailable",
            value=allow_demo_default,
            help="Shows clearly-labelled synthetic aircraft instead of an empty page.",
        )
        auto_refresh = st.toggle(
            "Auto-refresh",
            value=False,
            help=f"Re-fetch the snapshot every {refresh_seconds} seconds while the page is open.",
        )
        if st.button("Refresh now", width="stretch"):
            st.session_state["live_nonce"] = int(st.session_state.get("live_nonce", 0)) + 1
        st.caption(
            f"Auto-refresh interval: {refresh_seconds}s. Anonymous OpenSky access is "
            "rate limited; set OPENSKY_ACCESS_TOKEN for higher limits."
        )
        st.caption("Data: OpenSky Network (ADS-B). Positions may lag by seconds to minutes.")

    region = get_region(name, custom=custom, catalog=catalog)
    return {
        "region": region,
        "allow_demo": allow_demo,
        "auto_refresh": auto_refresh,
        "refresh_seconds": refresh_seconds,
    }


def _filters(frame: pd.DataFrame) -> dict[str, Any]:
    countries = sorted(frame["origin_country"].dropna().astype(str).unique().tolist())
    with st.sidebar:
        st.markdown("### Aircraft filters")
        ground_mode = st.radio(
            "Movement",
            ["All", "Airborne", "On ground"],
            horizontal=True,
        )
        altitude_range = st.slider(
            "Altitude band (ft)",
            min_value=0,
            max_value=45000,
            value=(0, 45000),
            step=500,
        )
        selected_countries = st.multiselect("Country of origin", countries, default=[])
        search = st.text_input("Callsign or ICAO24 contains", placeholder="e.g. AIC or 801640")
    on_ground = {"All": None, "Airborne": False, "On ground": True}[ground_mode]
    return {
        "countries": selected_countries,
        "on_ground": on_ground,
        "min_altitude_ft": altitude_range[0],
        "max_altitude_ft": altitude_range[1],
        "search": search,
    }


def _source_caption(meta: dict[str, Any]) -> None:
    fetched = pd.to_datetime(meta["fetched_at"]).strftime("%Y-%m-%d %H:%M:%S UTC")
    bounds = meta["region"]
    st.caption(
        f"Source: **{meta['source']}** | snapshot {fetched} | region "
        f"{bounds['name']} ({bounds['lamin']:.0f}–{bounds['lamax']:.0f}°N, "
        f"{bounds['lomin']:.0f}–{bounds['lomax']:.0f}°E)"
    )
    if meta.get("error"):
        logger.warning("Live tracking provider error: %s", meta["error"])
        st.warning(
            "Live provider request failed. The map below is a **demo snapshot**, "
            "not live traffic."
        )
    elif meta["source"] == "Demo fallback":
        st.info("Showing a clearly-labelled demo snapshot because no live data is available.")


def _kpis(snapshot: pd.DataFrame, filtered: pd.DataFrame) -> None:
    summary = summarise_states(filtered)
    total = summarise_states(snapshot)
    row = st.columns(5)
    row[0].metric("Aircraft in view", f"{summary['tracked']:,}")
    row[1].metric("Airborne", f"{summary['airborne']:,}", f"{summary['on_ground']:,} on ground")
    row[2].metric("Countries", f"{summary['countries']:,}")
    row[3].metric("Avg altitude", f"{summary['avg_altitude_ft'] / 1000:,.1f}k ft" if summary["airborne"] else "—")
    row[4].metric("Avg speed", f"{summary['avg_speed_kt']:,.0f} kt" if summary["airborne"] else "—")
    if summary["tracked"] != total["tracked"]:
        st.caption(
            f"Filters show {summary['tracked']:,} of {total['tracked']:,} aircraft in the snapshot."
        )


def _map(filtered: pd.DataFrame, region: Region) -> None:
    if filtered.empty:
        st.info("No aircraft match the current filters.")
        return

    plot = filtered.copy()
    plot["marker_size"] = plot["altitude_ft"].fillna(0.0).clip(lower=0.0)
    center = {
        "lat": (region.lamin + region.lamax) / 2.0,
        "lon": (region.lomin + region.lomax) / 2.0,
    }
    hover = {
        "altitude_ft": ":.0f",
        "speed_kt": ":.0f",
        "vertical_rate_fpm": ":.0f",
        "origin_country": True,
        "icao24": True,
        "squawk": True,
    }
    try:
        figure = px.scatter_map(
            plot,
            lat="latitude",
            lon="longitude",
            color="phase",
            size="marker_size",
            size_max=13,
            hover_name="flight",
            hover_data=hover,
            color_discrete_map=PHASE_COLORS,
            center=center,
            zoom=_zoom_for(region),
            map_style="open-street-map",
            height=560,
            labels={"phase": "Flight phase"},
        )
        figure.update_layout(
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "x": 0},
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    except Exception:  # pragma: no cover - defensive UI fallback
        logger.exception("Map tile rendering failed; using geographic scatter")
        st.caption("Map tiles are unavailable; using a geographic scatter instead.")
        fallback = px.scatter_geo(
            plot,
            lat="latitude",
            lon="longitude",
            color="phase",
            hover_name="flight",
            hover_data=hover,
            color_discrete_map=PHASE_COLORS,
            height=520,
        )
        fallback.update_layout(margin={"l": 0, "r": 0, "t": 0, "b": 0})
        st.plotly_chart(fallback, width="stretch", config={"displayModeBar": False})


def _breakdowns(filtered: pd.DataFrame) -> None:
    left, right = st.columns(2)
    with left:
        st.markdown("**Flight phase mix**")
        if filtered.empty:
            st.info("No aircraft to summarise.")
        else:
            phase = (
                filtered.groupby("phase", as_index=False)
                .size()
                .rename(columns={"size": "aircraft"})
                .sort_values("aircraft", ascending=False)
            )
            figure = px.bar(
                phase,
                x="phase",
                y="aircraft",
                color="phase",
                color_discrete_map=PHASE_COLORS,
                height=280,
            )
            figure.update_layout(showlegend=False, margin={"l": 0, "r": 0, "t": 10, "b": 0})
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    with right:
        st.markdown("**Altitude distribution (airborne)**")
        airborne = filtered[~filtered["on_ground"].fillna(False).astype(bool)]
        if airborne.empty:
            st.info("No airborne aircraft match the filters.")
        else:
            figure = px.histogram(
                airborne,
                x="altitude_ft",
                nbins=24,
                height=280,
                labels={"altitude_ft": "Altitude (ft)", "count": "Aircraft"},
            )
            figure.update_layout(margin={"l": 0, "r": 0, "t": 10, "b": 0}, yaxis_title="Aircraft")
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def _detail(filtered: pd.DataFrame) -> None:
    st.markdown("### Aircraft detail")
    if filtered.empty:
        st.info("No aircraft match the current filters.")
        return
    options = filtered["flight"].fillna(filtered["icao24"]).astype(str).tolist()
    labels = [
        f"{row.flight} · {row.origin_country} · {row.icao24}"
        for row in filtered[["flight", "origin_country", "icao24"]].itertuples(index=False)
    ]
    choice = st.selectbox("Select an aircraft", range(len(options)), format_func=lambda i: labels[i])
    row = filtered.iloc[choice]
    metrics = st.columns(4)
    metrics[0].metric("Altitude", f"{row['altitude_ft']:,.0f} ft" if pd.notna(row["altitude_ft"]) else "—")
    metrics[1].metric("Speed", f"{row['speed_kt']:,.0f} kt" if pd.notna(row["speed_kt"]) else "—")
    metrics[2].metric(
        "Vertical rate",
        f"{row['vertical_rate_fpm']:+,.0f} ft/min" if pd.notna(row["vertical_rate_fpm"]) else "—",
    )
    metrics[3].metric("Heading", f"{row['true_track_deg']:,.0f}°" if pd.notna(row["true_track_deg"]) else "—")
    age = row["position_age_s"]
    age_text = f"{age:,.0f}s" if pd.notna(age) else "unknown"
    squawk = row["squawk"] if pd.notna(row["squawk"]) else "—"
    flight = escape_markdown(row["flight"])
    icao24 = escape_markdown(row["icao24"])
    country = escape_markdown(row["origin_country"])
    phase = escape_markdown(row["phase"])
    squawk_text = escape_markdown(squawk)
    st.caption(
        f"Call sign **{flight}** · ICAO24 `{icao24}` · {country} · "
        f"phase {phase} · squawk {squawk_text} · position age {age_text}"
    )
    st.map(
        pd.DataFrame({"lat": [row["latitude"]], "lon": [row["longitude"]]}),
        zoom=6,
        height=260,
    )


def _table(filtered: pd.DataFrame, cfg) -> None:
    st.markdown("### Aircraft table")
    limit = int(cfg.get("live_tracking.max_aircraft_display", 1500))
    view = filtered.head(limit)
    if len(filtered) > limit:
        st.caption(f"Showing the first {limit:,} of {len(filtered):,} aircraft. Narrow the filters to see more.")
    if view.empty:
        st.info("No aircraft match the current filters.")
        return
    st.dataframe(
        view[list(TABLE_COLUMNS)].rename(columns=TABLE_COLUMNS),
        width="stretch",
        height=420,
        hide_index=True,
        column_config={
            "Altitude (ft)": st.column_config.NumberColumn(format="%.0f"),
            "Speed (kt)": st.column_config.NumberColumn(format="%.0f"),
            "Vertical rate (ft/min)": st.column_config.NumberColumn(format="%+.0f"),
            "Heading (°)": st.column_config.NumberColumn(format="%.0f"),
            "Position age (s)": st.column_config.NumberColumn(format="%.0f"),
        },
    )


def _render_live(cfg, region: Region, controls: dict[str, Any]) -> None:
    timeout = float(cfg.get("live_tracking.request_timeout_s", 20))
    nonce = int(st.session_state.get("live_nonce", 0))
    with st.spinner("Fetching live ADS-B states…"):
        snapshot, meta = _snapshot(region, timeout, controls["allow_demo"], nonce)

    if snapshot.empty:
        st.error("No live aircraft data is available for this region right now.")
        st.info(
            "OpenSky may be rate limiting anonymous requests. Wait a minute, "
            "enable the demo fallback, or set OPENSKY_ACCESS_TOKEN."
        )
        return

    _source_caption(meta)
    filters = _filters(snapshot)
    filtered = filter_states(
        snapshot,
        countries=filters["countries"],
        on_ground=filters["on_ground"],
        min_altitude_ft=filters["min_altitude_ft"],
        max_altitude_ft=filters["max_altitude_ft"],
        search=filters["search"],
    )

    _kpis(snapshot, filtered)
    _map(filtered, region)
    _breakdowns(filtered)
    _table(filtered, cfg)
    _detail(filtered)
    _closing_note(filtered, snapshot)


def _closing_note(filtered: pd.DataFrame, snapshot: pd.DataFrame) -> None:
    summary = summarise_states(filtered)
    if summary["tracked"] == 0:
        so_what(
            "No aircraft match the current filters. Widen the altitude band, clear the "
            "country or callsign filter, or switch the movement filter to All."
        )
        return
    busiest_country = (
        filtered["origin_country"].value_counts().index[0]
        if filtered["origin_country"].notna().any()
        else "unknown"
    )
    so_what(
        f"<b>{summary['tracked']:,} aircraft</b> are visible in this snapshot "
        f"({summary['airborne']:,} airborne, {summary['on_ground']:,} on ground) from "
        f"<b>{summary['countries']}</b> countries; the largest representation is "
        f"<b>{escape_html(busiest_country)}</b>. Use the map and table to sanity-check the picture, then "
        "pair it with the OTP and maintenance pages before making an operational decision. "
        "ADS-B coverage, latency and altitude are informational, not surveillance-grade."
    )
    assumption_box(
        "OpenSky Network data is crowd-sourced ADS-B. Coverage is uneven, positions can lag, "
        "and anonymous requests are rate limited (roughly 100 calls/day per IP in the public "
        "tier). Do not use this page for separation, dispatch or any safety-critical decision. "
        "A real OCC feed would come from the airline's surveillance/ADS-B provider with "
        "service-level guarantees."
    )


def _render_auto(cfg, region: Region, controls: dict[str, Any]) -> None:
    @st.fragment(run_every=controls["refresh_seconds"])
    def _body() -> None:
        _render_live(cfg, region, controls)

    _body()


def render() -> None:
    inject_css()
    cfg = get_config()
    controls = _region_controls(cfg)
    region = controls["region"]

    page_header(
        "Live flight tracking",
        "Current ADS-B positions from the OpenSky Network, filtered and mapped for situational awareness.",
    )

    if controls["auto_refresh"]:
        _render_auto(cfg, region, controls)
    else:
        _render_live(cfg, region, controls)
