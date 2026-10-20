"""Natural-language date/time parsing for reminders, calendars and scheduling.

Handles the phrases people actually say:

    "tomorrow at 9 am"        "in 45 minutes"        "next monday 3pm"
    "tonight at 8"            "on 12 october"        "every weekday at 7:30"
    "day after tomorrow noon" "in two hours"         "friday evening"

It is intentionally dependency-free and returns the *span* it consumed so the
caller can strip the time expression out of the reminder text
("remind me **tomorrow at 9am** to submit the assignment" -> "submit the assignment").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

WORDS_TO_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
    "half": 0.5, "quarter": 0.25, "couple": 2, "few": 3,
}

UNIT_SECONDS = {
    "second": 1, "sec": 1, "s": 1, "seconds": 1, "secs": 1,
    "minute": 60, "min": 60, "mins": 60, "minutes": 60, "m": 60,
    "hour": 3600, "hr": 3600, "hrs": 3600, "hours": 3600, "h": 3600,
    "day": 86400, "days": 86400, "d": 86400,
    "week": 604800, "weeks": 604800, "wk": 604800, "w": 604800,
    "fortnight": 1209600, "fortnights": 1209600,
    "month": 2592000, "months": 2592000,
    "year": 31536000, "years": 31536000,
}

DAY_PARTS = {
    "morning": dtime(9, 0),
    "noon": dtime(12, 0),
    "midday": dtime(12, 0),
    "afternoon": dtime(15, 0),
    "evening": dtime(18, 0),
    "night": dtime(21, 0),
    "tonight": dtime(20, 0),
    "midnight": dtime(0, 0),
    "eod": dtime(17, 0),
    "end of day": dtime(17, 0),
}

DEFAULT_TIME = dtime(9, 0)


@dataclass
class ParsedWhen:
    """The result of parsing a temporal expression."""

    when: datetime | None = None
    #: the exact substring that was consumed (so it can be removed from the task)
    spans: list[str] = field(default_factory=list)
    recurrence: str = ""            # "", "daily", "weekly", "weekdays", "weekend"
    end: datetime | None = None     # for durations / ranges
    duration: timedelta | None = None
    relative: bool = False
    confidence: float = 0.0
    raw: str = ""

    def __bool__(self) -> bool:
        return self.when is not None

    @property
    def matched_text(self) -> str:
        return " ".join(self.spans).strip()


def _to_number(token: str) -> float | None:
    token = token.strip().lower()
    if not token:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", token):
        return float(token)
    if token in WORDS_TO_NUM:
        return float(WORDS_TO_NUM[token])
    # "twenty five", "two and a half"
    parts = re.split(r"[\s-]+", token)
    if len(parts) == 2 and all(p in WORDS_TO_NUM for p in parts):
        first, second = WORDS_TO_NUM[parts[0]], WORDS_TO_NUM[parts[1]]
        if first >= 10 and second < 10:
            return float(first + second)
        return float(first * second) if second < 1 else float(first)
    return None


def parse_duration(text: str) -> timedelta | None:
    """`"45 minutes"` / `"2 hours"` / `"an hour and a half"` -> timedelta."""
    if not text:
        return None
    lowered = text.lower()
    matches = re.findall(r"(\d+(?:\.\d+)?|[a-z-]+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)",
                         lowered)
    total = 0.0
    for quantity, unit in matches:
        number = _to_number(quantity)
        if number is None:
            continue
        total += number * UNIT_SECONDS.get(unit.rstrip("s"), UNIT_SECONDS.get(unit, 0))
    if total <= 0:
        return None
    return timedelta(seconds=total)


MORNING_HINTS = (
    "morning", "meds", "medicine", "tablet", "gym", "workout", "yoga", "run", "breakfast",
    "standup", "stand-up", "school", "college", "class", "lecture", "pray", "walk", "juice",
    "daily", "every day", "everyday", "weekday",
)
EVENING_HINTS = ("evening", "night", "tonight", "dinner", "movie", "call", "party", "sleep")


def parse_time_of_day(text: str, *, context: str = "") -> dtime | None:
    """`"9:30 pm"`, `"15:00"`, `"noon"` -> time.

    `context` disambiguates bare hours ("at 7" -> 7am for a morning routine,
    7pm otherwise).
    """
    if not text:
        return None
    lowered = text.strip().lower()
    for phrase, value in DAY_PARTS.items():
        if lowered == phrase:
            return value

    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b", lowered)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not meridiem:
        hint = context.lower()
        morning = any(word in hint for word in MORNING_HINTS)
        evening = any(word in hint for word in EVENING_HINTS)
        if hour <= 6 and not morning:
            hour += 12      # "at 3" almost always means 3 pm
        elif hour in (7, 8):
            hour += 12 if (evening or not morning) and not morning else 0
        elif hour == 12 and evening:
            hour = 0
    if hour > 23 or minute > 59:
        return None
    return dtime(hour, minute)


@dataclass
class _DateHit:
    value: date
    span: str
    confidence: float


def _parse_date_part(text: str, now: datetime) -> _DateHit | None:
    lowered = text.lower()
    today = now.date()

    def hit(days: int, span: str, confidence: float = 0.95) -> _DateHit:
        return _DateHit(today + timedelta(days=days), span, confidence)

    if re.search(r"\bday after tomorrow\b", lowered):
        return hit(2, "day after tomorrow")
    if re.search(r"\btoday\b|\btonight\b", lowered):
        return hit(0, "today" if "today" in lowered else "tonight")
    if re.search(r"\btomorrow\b|\btmr\b|\btmrw\b", lowered):
        return hit(1, "tomorrow")
    if re.search(r"\byesterday\b", lowered):
        return hit(-1, "yesterday", 0.8)

    # "next monday" / "monday" / "this friday"
    weekday_match = re.search(
        r"\b(?:(next|this|coming|on)\s+)?(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|"
        r"friday|fri|saturday|sat|sunday|sun)\b",
        lowered,
    )
    if weekday_match:
        target = WEEKDAYS[weekday_match.group(2)]
        delta = (target - today.weekday()) % 7
        qualifier = weekday_match.group(1)
        if delta == 0 and qualifier != "this":
            delta = 7
        if qualifier == "next" and delta < 7:
            delta += 0 if delta > 0 else 7
        return hit(delta, weekday_match.group(0).strip(), 0.9)

    # "next week"
    if re.search(r"\bnext week\b", lowered):
        return hit(7, "next week", 0.6)

    # explicit calendar dates: 12 october / october 12 / 12/10 / 2026-10-12
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", lowered)
    if iso:
        try:
            return _DateHit(date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), iso.group(0), 0.98)
        except ValueError:
            pass

    month_first = re.search(
        r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|"
        r"october|oct|november|nov|december|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b",
        lowered,
    )
    if month_first:
        month = MONTHS[month_first.group(1)]
        day = int(month_first.group(2))
        year = int(month_first.group(3)) if month_first.group(3) else today.year
        try:
            candidate = date(year, month, day)
            if candidate < today and not month_first.group(3):
                candidate = date(year + 1, month, day)
            return _DateHit(candidate, month_first.group(0), 0.95)
        except ValueError:
            pass

    day_first = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(january|jan|february|feb|march|mar|april|apr|may|june|jun|"
        r"july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)\.?(?:,?\s*(\d{4}))?\b",
        lowered,
    )
    if day_first:
        day = int(day_first.group(1))
        month = MONTHS[day_first.group(2)]
        year = int(day_first.group(3)) if day_first.group(3) else today.year
        try:
            candidate = date(year, month, day)
            if candidate < today and not day_first.group(3):
                candidate = date(year + 1, month, day)
            return _DateHit(candidate, day_first.group(0), 0.95)
        except ValueError:
            pass

    numeric = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b", lowered)
    if numeric:
        first, second = int(numeric.group(1)), int(numeric.group(2))
        year = today.year
        if numeric.group(3):
            year = int(numeric.group(3))
            if year < 100:
                year += 2000
        for candidate in ((year, second, first), (year, first, second)):
            try:
                parsed = date(*candidate)
                if parsed >= today or numeric.group(3):
                    return _DateHit(parsed, numeric.group(0), 0.7)
            except ValueError:
                continue

    # "in 3 days" / "3 days from now"
    relative = re.search(
        r"\bin\s+(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple(?:\s+of)?|"
        r"few|half(?:\s+an?)?)\s+"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)\b",
        lowered,
    )
    if relative:
        quantity = relative.group(1).replace("couple of", "2").replace("couple", "2")
        number = _to_number(quantity)
        unit = relative.group(2).rstrip("s")
        if number is not None and unit in UNIT_SECONDS:
            seconds = number * UNIT_SECONDS[unit]
            return _DateHit((now + timedelta(seconds=seconds)).date(), relative.group(0), 0.9)

    return None


def _detect_recurrence(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\bevery\s*(single\s*)?day\b|\bdaily\b|\beach day\b|\beveryday\b", lowered):
        return "daily"
    if re.search(r"\bevery\s+(weekday|working day)\b|\bweekdays\b|\bmonday to friday\b|\bmon-fri\b", lowered):
        return "weekdays"
    if re.search(r"\bevery\s+week\b|\bweekly\b", lowered):
        return "weekly"
    if re.search(r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered):
        return "weekly"
    if re.search(r"\bweekends?\b", lowered) and "every" in lowered:
        return "weekend"
    return ""


TIME_PATTERNS: tuple[str, ...] = (
    r"\b(?:at|@)\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b",
    r"\b(\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?)\b",
    r"\b(?:at|in the)\s+(morning|noon|midday|afternoon|evening|night|midnight)\b",
    r"\b(tonight|midnight|noon)\b",
    r"\b(\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?))\b",
)


def parse_when(text: str, *, now: datetime | None = None) -> ParsedWhen:
    """Parse a free-form temporal expression out of `text`."""
    now = now or datetime.now()
    result = ParsedWhen(raw=text)
    if not text:
        return result

    lowered = text.lower()
    result.recurrence = _detect_recurrence(lowered)

    # machine-readable timestamps win outright: "2026-12-25T08:00", "2026-12-25 08:00:00"
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})[tT ](\d{2}:\d{2}(?::\d{2})?)\b", lowered)
    if iso:
        parsed_iso = parse_iso(f"{iso.group(1)}T{iso.group(2)}")
        if parsed_iso is not None:
            result.when = parsed_iso
            result.spans = [iso.group(0)]
            result.confidence = 0.99
            return result

    date_hit = _parse_date_part(lowered, now)
    time_of_day: dtime | None = None
    time_span = ""

    # Never read a date's own digits as a clock time ("2026-12-25 at 8am").
    clock_source = lowered
    if date_hit and date_hit.span and date_hit.span in lowered:
        clock_source = lowered.replace(date_hit.span, " ", 1)

    for pattern in TIME_PATTERNS:
        match = re.search(pattern, clock_source)
        if match:
            candidate = parse_time_of_day(
                match.group(1) if match.groups() else match.group(0), context=text
            )
            if candidate is not None:
                time_of_day = candidate
                time_span = match.group(0).strip()
                break

    # pure relative ("in 45 minutes", "in two hours", "in 3 days") keeps the current
    # clock time instead of snapping to a default hour
    relative = re.search(
        r"\b(?:in|after)\s+(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"fifteen|twenty|thirty|forty|forty-five|fifty|sixty|half an|quarter of an|couple of|few)\s*"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)\b",
        lowered,
    )
    if relative and (not date_hit or date_hit.span == relative.group(0)):
        duration = parse_duration(relative.group(0).replace("after", "in"))
        if duration:
            base = now + duration
            if time_of_day:  # "in 3 days at 9am"
                base = datetime.combine(base.date(), time_of_day)
            result.when = base
            result.relative = True
            result.duration = duration
            spans = [relative.group(0)]
            if time_span and time_span not in spans:
                spans.append(time_span)
            result.spans = spans
            result.confidence = 0.95
            return result

    if date_hit:
        base = datetime.combine(date_hit.value, time_of_day or DEFAULT_TIME)
        if not time_of_day:
            part_match = re.search(r"\b(morning|afternoon|evening|night|tonight|noon|midnight)\b", lowered)
            if part_match:
                base = datetime.combine(date_hit.value, DAY_PARTS[part_match.group(1)])
                time_span = part_match.group(0)
            else:
                base = datetime.combine(date_hit.value, DEFAULT_TIME)
        # "tomorrow at 9" with a past-looking time still means tomorrow
        if base <= now and not time_of_day:
            base = base.replace(hour=max(now.hour + 1, 0) % 24)
        result.when = base
        result.confidence = max(date_hit.confidence, 0.9 if time_of_day else 0.7)
        spans = []
        if date_hit.span:
            spans.append(date_hit.span)
        if time_span and time_span not in spans:
            spans.append(time_span)
        result.spans = spans
        return result

    if time_of_day:
        base = datetime.combine(now.date(), time_of_day)
        if base <= now:
            base += timedelta(days=1)
        result.when = base
        result.spans = [time_span]
        result.confidence = 0.75
        return result

    return result


def parse_range(text: str, *, now: datetime | None = None) -> tuple[ParsedWhen, ParsedWhen]:
    """Parse "from 3pm to 4pm" / "for 2 hours" style ranges."""
    now = now or datetime.now()
    start = parse_when(text, now=now)
    end = ParsedWhen(raw=text)
    lowered = text.lower()

    duration = None
    for_match = re.search(r"\bfor\s+(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|ten|fifteen|thirty|half an)\s*"
                          r"(minutes?|mins?|hours?|hrs?|days?)\b", lowered)
    if for_match:
        duration = parse_duration(for_match.group(0).replace("for", "in"))
        end.spans = [for_match.group(0)]
    else:
        until = re.search(r"\b(?:to|until|till|-|–)\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b", lowered)
        if until and start.when:
            end_time = parse_time_of_day(until.group(1), context=text)
            if end_time:
                end.when = datetime.combine(start.when.date(), end_time)
                if end.when <= start.when:
                    end.when += timedelta(days=1)
                end.spans = [until.group(0)]

    if duration and start.when and end.when is None:
        end.when = start.when + duration
        end.duration = duration
    if end.when and start.when:
        end.duration = end.when - start.when
    return start, end


def strip_time_expression(text: str, parsed: ParsedWhen) -> str:
    """Remove the temporal phrase from a reminder sentence."""
    cleaned = text
    lowered = cleaned.lower()
    for span in list(parsed.spans):
        if not span:
            continue
        index = lowered.find(span.lower())
        if index >= 0:
            cleaned = cleaned[:index] + " " + cleaned[index + len(span):]
            lowered = cleaned.lower()

    patterns = [
        r"\b(?:please\s+)?(?:hey\s+|ok\s+|jarvis\s*[,-]?\s*)?remind\s+me\s+(?:to|that|about)?\s*",
        r"\bremind\s+me\s*",
        r"\bset\s+(?:a|an)?\s*reminder\s*(?:to|for|that)?\s*",
        r"\b(?:i\s+)?need\s+(?:a\s+)?reminder\s*(?:to|for)?\s*",
        r"\badd\s+(?:a\s+)?reminder\s*(?:to|for)?\s*",
        r"\bput\s+(?:a\s+)?reminder\s*(?:to|for)?\s*",
        r"\bevery\s+(?:day|weekday|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(?:at|in|on|by|from|to|until|till|for|after|the|of|and)\b\s*$",
        r"^\s*(?:at|in|on|by|from|to|until|till|for|after|the|of|and)\s+",
        r"\s{2,}",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([,.;!?])", r"\1", cleaned)
    cleaned = re.sub(r"^(?:to|about|that|for|at|on|in|by)\s+", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:to|about|that|for)\s*$", "", cleaned.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ,;:-–.")
    return cleaned.strip() or text.strip()


def extract_reminder(text: str, *, now: datetime | None = None) -> tuple[ParsedWhen, str]:
    """Convenience: (when, task_text) from "remind me tomorrow at 9 to X"."""
    parsed = parse_when(text, now=now)
    return parsed, strip_time_expression(text, parsed)


# ---------------------------------------------------------------------------
# Humanisation — what JARVIS says back
# ---------------------------------------------------------------------------


def humanize(when: datetime | None, *, now: datetime | None = None, with_year: bool = False) -> str:
    if when is None:
        return "sometime"
    now = now or datetime.now()
    today = now.date()
    delta_days = (when.date() - today).days

    if delta_days == 0:
        day = "today"
    elif delta_days == 1:
        day = "tomorrow"
    elif delta_days == -1:
        day = "yesterday"
    elif delta_days == 2:
        day = "the day after tomorrow"
    elif 2 < delta_days < 7:
        day = when.strftime("%A")
    else:
        day = when.strftime("%d %b" + (", %Y" if (with_year or when.year != now.year) else ""))

    return f"{day} at {when.strftime('%I:%M %p').lstrip('0')}"


def humanize_duration(delta: timedelta | None) -> str:
    if not delta:
        return ""
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total} seconds"
    if total < 3600:
        minutes = round(total / 60)
        return f"{minutes} minute" + ("s" if minutes != 1 else "")
    if total < 86400:
        hours, remainder = divmod(total, 3600)
        minutes = round(remainder / 60)
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours} hour" + ("s" if hours != 1 else "")
    days = round(total / 86400)
    return f"{days} day" + ("s" if days != 1 else "")


def countdown(when: datetime, *, now: datetime | None = None) -> str:
    now = now or datetime.now()
    delta = when - now
    if delta.total_seconds() <= 0:
        return "now"
    return "in " + humanize_duration(delta)


def recurrence_matches(recurrence: str, moment: datetime) -> bool:
    if recurrence in {"", "none", "once"}:
        return True
    if recurrence == "daily":
        return True
    if recurrence == "weekdays":
        return moment.weekday() < 5
    if recurrence == "weekend":
        return moment.weekday() >= 5
    if recurrence == "weekly":
        return True
    return True


def next_occurrence(recurrence: str, previous: datetime) -> datetime | None:
    """Advance a recurring reminder to its next fire time."""
    if recurrence in {"", "none", "once"}:
        return None
    if recurrence == "daily":
        return previous + timedelta(days=1)
    if recurrence == "weekly":
        return previous + timedelta(weeks=1)
    if recurrence == "weekdays":
        candidate = previous + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
    if recurrence == "weekend":
        candidate = previous + timedelta(days=1)
        while candidate.weekday() < 5:
            candidate += timedelta(days=1)
        return candidate
    return None


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def as_iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def guess_duration_from_title(title: str) -> timedelta:
    """Default meeting length: 1 hour for meetings/interviews, 30 min otherwise."""
    lowered = (title or "").lower()
    if any(word in lowered for word in ("interview", "meeting", "class", "lecture", "movie", "flight", "call")):
        return timedelta(hours=1)
    if any(word in lowered for word in ("gym", "workout", "run", "walk", "break", "standup", "stand-up")):
        return timedelta(minutes=30)
    return timedelta(minutes=45)


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def day_bounds(day: date | None = None, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now()
    base = day or now.date()
    start = datetime.combine(base, dtime(0, 0))
    return start, start + timedelta(days=1)


def week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now()
    start = datetime.combine(now.date() - timedelta(days=now.weekday()), dtime(0, 0))
    return start, start + timedelta(days=7)


__all__ = [
    "ParsedWhen", "parse_when", "parse_range", "parse_duration", "parse_time_of_day",
    "extract_reminder", "strip_time_expression", "humanize", "humanize_duration", "countdown",
    "next_occurrence", "recurrence_matches", "guess_duration_from_title", "day_bounds",
    "week_bounds", "month_bounds", "as_iso", "parse_iso",
]
