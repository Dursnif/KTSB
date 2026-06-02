"""Time and date tool for Kåre."""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

_WEEKDAYS_NO = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
_MONTHS_NO = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]


class TimeTool:
    """Handles time and date queries."""

    def handle(self, params: dict) -> str:
        """Handle a get_time action. Returns a human-readable string."""
        now = datetime.now()
        query = params.get("query", "current_time")

        if query == "current_date":
            weekday = _WEEKDAYS_NO[now.weekday()]
            month = _MONTHS_NO[now.month - 1]
            return f"I dag er det {weekday} {now.day}. {month} {now.year}."

        # Default: current time
        return f"Klokken er {now.strftime('%H:%M')}."
