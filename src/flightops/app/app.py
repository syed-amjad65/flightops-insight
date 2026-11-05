"""FlightOps Insight Streamlit application entrypoint."""

from __future__ import annotations

import streamlit as st

from flightops.app.pages import (
    fleet_health,
    live_tracking,
    optimizer,
    overview,
    predictor,
    scenarios,
)


def main() -> None:
    st.set_page_config(
        page_title="FlightOps Insight",
        page_icon=":material/flight:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    pages = {
        "Flight operations": [
            st.Page(overview.render, title="OTP overview", icon=":material/insights:", url_path="otp-overview"),
            st.Page(
                live_tracking.render,
                title="Live flight tracking",
                icon=":material/radar:",
                url_path="live-traffic",
            ),
            st.Page(predictor.render, title="Delay-risk predictor", icon=":material/query_stats:", url_path="delay-risk"),
        ],
        "Maintenance": [
            st.Page(fleet_health.render, title="Fleet health & RUL", icon=":material/health_and_safety:", url_path="fleet-health"),
            st.Page(optimizer.render, title="Maintenance optimizer", icon=":material/build:", url_path="maintenance-plan"),
            st.Page(scenarios.render, title="Scenario comparison", icon=":material/compare_arrows:", url_path="scenarios"),
        ],
    }
    navigation = st.navigation(pages)
    with st.sidebar:
        st.markdown("---")
        st.caption(
            "Prototype on public BTS 2024 and NASA C-MAPSS data. Synthetic fleet for the optimizer. "
            "Not for operational use without airline validation."
        )
    navigation.run()


if __name__ == "__main__":
    main()
