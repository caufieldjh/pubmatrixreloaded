"""Tests for the quiet hours window."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pubmatrix.schedule import QuietHours

ET = ZoneInfo("America/New_York")


def eastern(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


@pytest.mark.parametrize(
    "moment, expected",
    [
        (eastern(5, 13), False),  # Thursday afternoon
        (eastern(5, 20, 59), False),
        (eastern(5, 21), True),
        (eastern(6, 4, 59), True),  # Friday, before dawn
        (eastern(6, 5), False),
        (eastern(7, 13), True),  # Saturday
        (eastern(8, 13), True),  # Sunday
        (eastern(9, 4, 59), True),  # Monday, before dawn
        (eastern(9, 5), False),
    ],
)
def test_window(moment, expected):
    assert QuietHours().is_open(moment) is expected


def test_opens_tonight_at_nine_eastern():
    opens = QuietHours().next_open(eastern(5, 13))
    assert opens == eastern(5, 21)


def test_other_time_zones_are_converted():
    pacific = datetime(2026, 11, 5, 18, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert QuietHours().is_open(pacific) is True  # 21:30 Eastern
