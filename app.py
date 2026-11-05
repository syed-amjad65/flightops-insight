"""FlightOps Insight Streamlit launcher.

Run from the repository root with::

    streamlit run app.py

The launcher adds ``src`` to ``sys.path`` so the app works from a source
checkout without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flightops.app.app import main  # noqa: E402

main()
