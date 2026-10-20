"""Natural-language time parsing — the backbone of reminders and calendar."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jarvis.core.timeparse import (
    countdown,
    extract_reminder,
    humanize,
    humanize_duration,
    next_occurrence,
    parse_duration,
    parse_range,
    parse_time_of_day,
    parse_when,
)

NOW = datetime(2026, 9, 30, 14, 15)  # a Wednesday afternoon


@pytest.mark.parametrize(
    "text,expected",
    [
        ("tomorrow at 9 AM", datetime(2026, 10, 1, 9, 0)),
        ("tomorrow at 9am", datetime(2026, 10, 1, 9, 0)),
        ("today at 5", datetime(2026, 9, 30, 17, 0)),
        ("at 15:30 today", datetime(2026, 9, 30, 15, 30)),
        ("tonight at 8", datetime(2026, 9, 30, 20, 0)),
        ("day after tomorrow noon", datetime(2026, 10, 2, 12, 0)),
        ("next monday at 3pm", datetime(2026, 10, 5, 15, 0)),
        ("on 12 october", datetime(2026, 10, 12, 9, 0)),
        ("october 12 at 6pm", datetime(2026, 10, 12, 18, 0)),
        ("2026-12-25T08:00", datetime(2026, 12, 25, 8, 0)),
        ("in 45 minutes", datetime(2026, 9, 30, 15, 0)),
        ("in two hours", datetime(2026, 9, 30, 16, 15)),
        ("in 3 days", datetime(2026, 10, 3, 14, 15)),
    ],
)
def test_parse_when(text: str, expected: datetime) -> None:
    parsed = parse_when(text, now=NOW)
    assert parsed.when is not None, text
    assert parsed.when.replace(second=0, microsecond=0) == expected.replace(second=0, microsecond=0)


def test_relative_minutes_are_exact() -> None:
    parsed = parse_when("in 90 seconds", now=NOW)
    assert parsed.when == NOW + timedelta(seconds=90)
    assert parsed.relative is True


def test_past_time_rolls_to_tomorrow() -> None:
    parsed = parse_when("at 9am", now=NOW)
    assert parsed.when == datetime(2026, 10, 1, 9, 0)


def test_morning_context_prefers_am() -> None:
    assert parse_time_of_day("7:30", context="every weekday at 7:30 take my meds") == parse_time_of_day("7:30 am")
    assert parse_time_of_day("8", context="dinner at 8") == parse_time_of_day("8 pm")


@pytest.mark.parametrize(
    "text,task,when",
    [
        ("remind me tomorrow at 9 AM to submit my assignment", "submit my assignment", datetime(2026, 10, 1, 9, 0)),
        ("remind me to buy milk at 5 pm today", "buy milk", datetime(2026, 9, 30, 17, 0)),
        ("set a reminder every weekday at 7:30 to take my meds", "take my meds", datetime(2026, 10, 1, 7, 30)),
        ("remind me in 20 minutes to stretch", "stretch", datetime(2026, 9, 30, 14, 35)),
        ("jarvis, remind me on 12 october to renew my passport", "renew my passport", datetime(2026, 10, 12, 9, 0)),
    ],
)
def test_extract_reminder(text: str, task: str, when: datetime) -> None:
    parsed, extracted = extract_reminder(text, now=NOW)
    assert parsed.when is not None
    assert parsed.when.replace(second=0, microsecond=0) == when
    assert extracted.lower() == task.lower()


def test_recurrence_detection() -> None:
    assert parse_when("every day at 8am", now=NOW).recurrence == "daily"
    assert parse_when("every weekday at 7:30", now=NOW).recurrence == "weekdays"
    assert parse_when("weekly on monday", now=NOW).recurrence == "weekly"
    assert parse_when("tomorrow at 9", now=NOW).recurrence == ""


def test_next_occurrence_advances_correctly() -> None:
    friday = datetime(2026, 10, 2, 9, 0)
    assert next_occurrence("daily", friday) == friday + timedelta(days=1)
    assert next_occurrence("weekly", friday) == friday + timedelta(weeks=1)
    # weekday recurrence skips the weekend
    assert next_occurrence("weekdays", friday) == datetime(2026, 10, 5, 9, 0)
    assert next_occurrence("", friday) is None


def test_ranges_and_durations() -> None:
    start, end = parse_range("project review from 3pm to 4pm tomorrow", now=NOW)
    assert start.when == datetime(2026, 10, 1, 15, 0)
    assert end.when == datetime(2026, 10, 1, 16, 0)
    assert end.duration == timedelta(hours=1)

    start, end = parse_range("gym for 2 hours tomorrow at 6pm", now=NOW)
    assert start.when == datetime(2026, 10, 1, 18, 0)
    assert end.when == datetime(2026, 10, 1, 20, 0)

    assert parse_duration("45 minutes") == timedelta(minutes=45)
    assert parse_duration("2 hours") == timedelta(hours=2)


def test_humanisation_reads_well() -> None:
    assert humanize(datetime(2026, 10, 1, 9, 0), now=NOW) == "tomorrow at 9:00 AM"
    assert humanize(datetime(2026, 9, 30, 17, 0), now=NOW) == "today at 5:00 PM"
    assert humanize(datetime(2026, 10, 5, 15, 0), now=NOW) == "Monday at 3:00 PM"
    assert humanize_duration(timedelta(minutes=90)) == "1h 30m"
    assert humanize_duration(timedelta(minutes=1)) == "1 minute"
    assert countdown(datetime(2026, 9, 30, 15, 15), now=NOW) == "in 1 hour"


def test_unparseable_text_returns_nothing() -> None:
    assert parse_when("whenever you feel like it", now=NOW).when is None
