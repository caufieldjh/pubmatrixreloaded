"""Quiet hours: when a large run should be talking to NCBI.

NCBI asks E-utilities users to keep large jobs to weekends or 9 PM to 5 AM
Eastern on weekdays. PubTator3 publishes only a rate limit, but it is hosted by
NCBI, so the same window is used as a courtesy.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
OPENS = time(21, 0)
CLOSES = time(5, 0)


class QuietHours:
    def is_open(self, moment: datetime) -> bool:
        local = moment.astimezone(EASTERN)
        if local.weekday() >= 5:
            return True
        return local.time() >= OPENS or local.time() < CLOSES

    def next_open(self, moment: datetime) -> datetime:
        """When the window next opens. Only meaningful while it is closed.

        The closed span is always the same weekday, so today's opening is next.
        """
        local = moment.astimezone(EASTERN)
        return local.replace(hour=OPENS.hour, minute=0, second=0, microsecond=0)
