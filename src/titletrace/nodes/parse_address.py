"""parse_address node -- first node in the TitleTrace graph.

Extracts state and city from the raw address string. Rejects addresses outside
PA and NJ since the data source coverage is PA/NJ only. A simple regex parse
is sufficient; this is not a geocoding node (geocoding happens in fetch_parcel).
"""

from __future__ import annotations

import re

from titletrace.state import TraceState

# Two-letter state abbreviations supported in v1.
_SUPPORTED_STATES = {"PA", "NJ"}

_STATE_RE = re.compile(
    r",\s*(?P<city>[^,]+),?\s+(?P<state>PA|NJ|pa|nj)\s+(?P<zip>\d{5}(?:-\d{4})?)",
    re.IGNORECASE,
)


def parse_address(state: TraceState) -> dict:
    """Extract city and state from the raw address. Sets error if state is
    unsupported or the address format is unrecognizable."""
    raw = state["raw_address"].strip()
    m = _STATE_RE.search(raw)
    if not m:
        return {
            "error": (
                f"Could not parse PA or NJ address from '{raw}'. "
                "Expected format: '123 Main St, Philadelphia, PA 19103'."
            )
        }
    parsed_state = m.group("state").upper()
    if parsed_state not in _SUPPORTED_STATES:
        return {
            "error": f"State '{parsed_state}' is not supported. TitleTrace covers PA and NJ only."
        }
    return {
        "city": m.group("city").strip(),
        "state": parsed_state,
        "error": None,
    }
