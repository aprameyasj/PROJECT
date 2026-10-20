"""Offline intent engine — JARVIS without an API key.

This is a real, deterministic NLU layer, not a toy:

* It maps natural speech ("Jarvis, close this tab", "remind me tomorrow at 9 to
  submit my assignment") onto **the same ToolCall objects** an LLM would emit.
* It is used when no API key is configured, when the API is unreachable, and in
  the test-suite (which must not depend on a network).
* Every rule reports a confidence; anything below the threshold falls through to
  a conversational reply instead of guessing an action.

Rules are data + a small extraction function, so extending JARVIS offline means
adding one entry to `RULES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..core.timeparse import (
    extract_reminder,
    guess_duration_from_title,
    humanize,
    humanize_duration,
    parse_range,
)
from ..core.types import Message, ToolCall
from .base import Decision

# ---------------------------------------------------------------------------
# rule machinery
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    intent: str
    tool: str
    #: regexes tried in order; the first match wins
    patterns: tuple[re.Pattern[str], ...]
    #: (match, text) -> arguments dict | list[ToolCall] | None
    build: Callable[[re.Match[str], str], Any] | None = None
    confidence: float = 0.85
    #: extra tools that may be produced
    extra_tools: tuple[str, ...] = ()
    notes: str = ""

    def match(self, text: str) -> tuple[re.Match[str] | None, float]:
        for pattern in self.patterns:
            found = pattern.search(text)
            if found:
                return found, self.confidence
        return None, 0.0


def rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _clean(text: str) -> str:
    """Normalise an utterance: strip the wake word, punctuation, filler."""
    lowered = text.strip().lower()
    lowered = re.sub(r"^(hey |hi |ok |okay |yo )?jarvis[\s,:.-]*", "", lowered)
    lowered = re.sub(r"^(please |pls |kindly |could you |can you |would you )+", "", lowered)
    lowered = re.sub(r"[.!?]+$", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


APP_WORDS = (
    "vs ?code|vscode|visual studio code|code|cursor|chrome|google chrome|firefox|edge|"
    "safari|browser|terminal|command prompt|cmd|powershell|windows terminal|explorer|"
    "file explorer|files|finder|calculator|calc|notepad|text ?editor|settings|"
    "system settings|preferences|task manager|activity monitor|word|excel|powerpoint|"
    "spotify|music|slack|discord|notion|zoom|postman|docker(?: desktop)?|pycharm|"
    "intellij|idea|android studio|blender|obs|vlc|media player|paint|photos|gallery|"
    "whatsapp|steam|maps|calendar|mail|email"
)

# ---------------------------------------------------------------------------
# argument builders
# ---------------------------------------------------------------------------


def _app_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    """Emit the canonical app id ("VS Code" -> "vscode") so traces and audits
    are consistent; the tool itself stays tolerant of free-form names."""
    from ..backends.apps import canonicalise

    groups = match.groupdict()
    name = (groups.get("app") or groups.get("app2") or "").strip()
    return {"application": canonicalise(name) or name} if name else {}


def _text_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    value = match.group("text") or match.group("quote") or ""
    return {"text": value.strip().strip('"\'')}


def _key_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    key = (match.group("key") or "").strip().lower().replace(" ", "")
    mapping = {
        "enter": "enter", "return": "enter", "escape": "esc", "esc": "esc", "tab": "tab",
        "space": "space", "backspace": "backspace", "delete": "delete", "up": "up",
        "down": "down", "left": "left", "right": "right", "home": "home", "end": "end",
        "pageup": "pageup", "pagedown": "pagedown", "f1": "f1", "f2": "f2", "f3": "f3",
        "f4": "f4", "f5": "f5", "f11": "f11", "f12": "f12",
    }
    return {"key": mapping.get(key, key)}


def _search_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    query = (match.group("query") or "").strip().strip('"\'')
    query = re.sub(r"^(?:on|in|using)\s+(?:the\s+)?(?:web|internet|google|duckduckgo|bing)\s*$", "", query)
    return {"query": query}


def _url_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    url = (match.group("url") or "").strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url):
        url = "https://" + url
    return {"url": url}


def _reminder_arguments(match: re.Match[str], text: str, *, now: datetime | None = None) -> dict[str, Any]:
    parsed, task = extract_reminder(text, now=now)
    if parsed.when is None:
        # "remind me to buy milk" with no time -> default to 1 hour from now
        default = (now or datetime.now()).replace(microsecond=0) + _one_hour()
        return {"task": task or text, "when": default.isoformat(timespec="minutes"),
                "recurrence": "", "_missing_time": True}
    return {
        "task": task or text,
        "when": parsed.when.isoformat(timespec="minutes"),
        "recurrence": parsed.recurrence,
        "_matched": parsed.matched_text,
        "_human": humanize(parsed.when, now=now),
    }


def _one_hour():
    from datetime import timedelta

    return timedelta(hours=1)


def _event_arguments(match: re.Match[str], text: str, *, now: datetime | None = None) -> dict[str, Any]:
    start, end = parse_range(text, now=now)
    title = _event_title(text, start.spans + end.spans)
    if start.when is None:
        return {"title": title, "_missing_time": True}
    finish = end.when or (start.when + guess_duration_from_title(title))
    return {
        "title": title,
        "starts_at": start.when.isoformat(timespec="minutes"),
        "ends_at": finish.isoformat(timespec="minutes"),
        "location": (match.groupdict().get("place") or "").strip() if match.groupdict() else "",
        "_human": humanize(start.when, now=now),
    }


def _event_title(text: str, spans: Iterable[str]) -> str:
    cleaned = text
    for span in spans:
        if span:
            cleaned = cleaned.replace(span, " ", 1)
    cleaned = re.sub(
        r"\b(?:please\s+)?(?:hey\s+|ok\s+|jarvis[\s,-]*)?(?:add|create|schedule|book|set up|make|put)\s+"
        r"(?:a|an|the)?\s*(?:calendar\s+)?(?:event|meeting|appointment|entry|slot)?\s*(?:for|on|at|to|named|called)?\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:for|from|to|until|till|at|on|in|the|a|an|of|and)\b\s*$", "", cleaned.strip(),
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:for|from|to|until|till|at|on|in|the|a|an|of|and)\s+", "", cleaned.strip(),
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:-")
    return cleaned or "Event"


FILE_HINTS = (
    "file", "files", "folder", "directory", "document", "doc", "docs", "photo", "photos", "image",
    "images", "picture", "pictures", "screenshot", "screenshots", "download", "downloads", "note",
    "notes", "resume", "cv", "report", "reports", "invoice", "invoices", "presentation", "slides",
    "spreadsheet", "sheet", "pdf", "zip", "csv", "desktop", "documents",
)
FILE_EXTENSIONS = re.compile(
    r"\.(?:pdf|docx?|xlsx?|pptx?|txt|md|rtf|odt|py|js|ts|tsx|jsx|json|ya?ml|toml|ini|cfg|sh|bat|"
    r"png|jpe?g|gif|webp|svg|zip|rar|7z|tar|gz|csv|mp3|mp4|mov|mkv|wav)\b",
    re.IGNORECASE,
)


def _looks_like_file_query(text: str) -> bool:
    """True when a `find ...` utterance means *the filesystem*, not the web."""
    lowered = text.lower()
    if FILE_EXTENSIONS.search(lowered):
        return True
    if re.search(r"\b(?:on my|in my|from my|inside)\s+(?:desktop|downloads|documents|folder)\b", lowered):
        return True
    return any(hint in lowered.split() for hint in FILE_HINTS)


def _file_query_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    groups = match.groupdict()
    query = (groups.get("query") or groups.get("query2") or "").strip().strip("\"'”“")
    query = re.sub(r"^(?:the|my|a|an)\s+", "", query, flags=re.IGNORECASE).strip()
    if not _looks_like_file_query(text) and not FILE_EXTENSIONS.search(query):
        # Not a filesystem request after all: hand it to the web search rule.
        return {"_abort": True}
    return {"query": query}


def _window_target_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    groups = match.groupdict()
    target = (groups.get("target") or groups.get("target2") or groups.get("target3") or "").strip()
    target = re.sub(r"^(?:the|my|a|an)\s+", "", target, flags=re.IGNORECASE)
    target = re.sub(r"\s+(?:window|app|application)$", "", target, flags=re.IGNORECASE).strip()
    return {"title": target}


def _percent_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    raw = (match.group("amount") or "50").strip().lower()
    words = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
             "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "max": 100,
             "half": 50, "quarter": 25, "full": 100}
    value = words.get(raw, None)
    if value is None:
        try:
            value = int(re.sub(r"\D", "", raw) or 50)
        except ValueError:
            value = 50
    return {"percent": value}


# ---------------------------------------------------------------------------
# the rule table
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    # ---------------- computer: applications ----------------
    Rule(
        "open_application",
        "open_application",
        rx(
            rf"^(?:open|launch|start|run|fire up|bring up|load)\s+(?:the\s+|my\s+)?(?P<app>{APP_WORDS})(?:\s+app|\s+application)?$",
            rf"\bopen\s+(?:up\s+)?(?P<app2>{APP_WORDS})\b",
        ),
        _app_arguments,
        confidence=0.95,
    ),
    Rule(
        "close_application",
        "close_application",
        rx(
            rf"^(?:close|quit|exit|kill|terminate|shut)\s+(?:the\s+|my\s+)?(?P<app>{APP_WORDS})(?:\s+app|\s+application)?$",
            rf"\b(?:close|quit|kill|terminate)\s+(?:down\s+)?(?P<app2>{APP_WORDS})\b",
        ),
        _app_arguments,
        confidence=0.93,
    ),
    # ---------------- computer: browser tabs ----------------
    Rule(
        "close_tab",
        "close_current_tab",
        rx(
            r"^(?:close|kill)\s+(?:this|the|current|active)?\s*tab$",
            r"\bclose\s+(?:the\s+)?(?:current|active|this)\s+(?:browser\s+)?tab\b",
            r"^(?:close|kill)\s+this\s+(?:browser\s+)?(?:tab|window|page)$",
        ),
        None,
        confidence=0.97,
    ),
    Rule(
        "new_tab",
        "new_tab",
        rx(r"\bopen\s+(?:a\s+)?new\s+tab\b", r"^(?:new|add)\s+tab$"),
        None,
        confidence=0.9,
    ),
    Rule(
        "reopen_tab",
        "reopen_closed_tab",
        rx(r"\b(?:reopen|restore|bring back|undo)\s+(?:the\s+)?(?:closed\s+)?(?:last\s+)?tab\b",
           r"\bundo\s+close\s+tab\b"),
        None,
        confidence=0.88,
    ),
    # ---------------- computer: input ----------------
    Rule(
        "type_text",
        "type_text",
        rx(
            r"^(?:type|write|enter|input|paste)\s+(?:out\s+|in\s+)?[\"“']?(?P<text>.+?)[\"”']?$",
            r"\btype\s+[\"“'](?P<quote>.+?)[\"”']",
        ),
        _text_arguments,
        confidence=0.82,
    ),
    Rule(
        "press_key",
        "press_key",
        rx(
            r"^(?:press|hit|tap|push)\s+(?:the\s+)?(?P<key>enter|return|escape|esc|tab|space|backspace|"
            r"delete|up|down|left|right|home|end|pageup|pagedown|f\d{1,2})\s*(?:key|button)?$",
            r"\bpress\s+(?:the\s+)?(?P<key2>[a-z0-9]+)\s+key\b",
        ),
        _key_arguments,
        confidence=0.88,
    ),
    Rule(
        "hotkey",
        "press_hotkey",
        rx(
            r"\b(?:press|hit|do|send)\s+(?P<combo>(?:ctrl|control|cmd|command|alt|option|shift|super|win)\s*\+\s*"
            r"[a-z0-9+ ]+?)\s*$",
            r"^(?P<combo2>(?:ctrl|cmd|alt|shift)(?:\s*\+\s*[a-z0-9]+)+)$",
        ),
        lambda m, t: {"keys": re.findall(r"[a-z0-9]+", (m.group("combo") or m.group("combo2") or "").lower())},
        confidence=0.86,
    ),
    Rule(
        "screenshot",
        "take_screenshot",
        rx(
            r"\b(?:take|grab|capture|snap|shoot)\s+(?:a\s+)?(?:screen\s*shot|screenshot|snap|screen capture)\b",
            r"^screenshot$",
            r"\bshow\s+me\s+(?:my|the)\s+screen\b",
        ),
        None,
        confidence=0.96,
    ),
    Rule(
        "move_mouse",
        "move_mouse",
        rx(r"\bmove\s+(?:the\s+)?(?:mouse|cursor|pointer)\s+(?:to\s+)?(?P<x>\d+)\s*[, ]+\s*(?P<y>\d+)\b"),
        lambda m, t: {"x": int(m.group("x")), "y": int(m.group("y"))},
        confidence=0.85,
    ),
    Rule(
        "click",
        "click",
        rx(
            r"^(?:click|left click|double click|double-click|right click|right-click)\s*(?:at\s*)?"
            r"(?P<x>\d+)\s*[, ]+\s*(?P<y>\d+)$",
            r"\bclick\s+(?:at\s+)?(?P<x2>\d+)\s*[, ]+\s*(?P<y2>\d+)\b",
        ),
        lambda m, t: {
            "x": int(m.group("x") or m.group("x2")),
            "y": int(m.group("y") or m.group("y2")),
            "button": "right" if "right" in t else "left",
            "clicks": 2 if "double" in t else 1,
        },
        confidence=0.9,
    ),
    Rule(
        "click_element",
        "click_element",
        rx(
            r"\b(?:click|press|hit|tap|select)\s+(?:the\s+|on\s+the\s+|on\s+)?[\"“']?(?P<label>[\w \-'/&+.]{2,40}?)\s*"
            r"[\"”']?\s*(?:button|link|icon|option|menu item|tab|checkbox)?\s*$",
        ),
        lambda m, t: {"label": (m.group("label") or "").strip(),
                      "button": "right" if "right" in t else "left"},
        confidence=0.55,   # needs vision; lower priority than everything else
        extra_tools=("describe_screen",),
        notes="vision-assisted click",
    ),
    Rule(
        "scroll",
        "scroll",
        rx(r"\bscroll\s+(?P<direction>up|down|left|right)?\s*(?P<amount>\d+)?\b"),
        lambda m, t: {
            "amount": int(m.group("amount") or 3) * (1 if (m.group("direction") or "down") == "up" else -1)
        },
        confidence=0.9,
    ),
    Rule(
        "switch_window",
        "switch_window",
        rx(r"\b(?:switch|go)\s+(?:to\s+)?(?:the\s+)?(?:window|app|application)\s*(?P<target>.*)$",
           # "switch to the chrome window", "go to the spotify app"
           r"\b(?:switch|go|change|flip)\s+to\s+(?:the\s+|my\s+)?(?P<target2>[\w .\-]+?)\s+"
           r"(?:window|app|application)\b",
           # "focus chrome", "bring up the terminal window", "activate spotify"
           r"\b(?:focus|activate|bring (?:up|to front)|raise)\s+(?:the\s+|my\s+)?(?P<target3>[\w .\-]+?)"
           r"\s*(?:window|app|application)?\s*$",
           r"^(?:alt|cmd)\s*\+\s*tab$", r"\bnext\s+window\b"),
        lambda m, t: _window_target_arguments(m, t),
        confidence=0.86,
    ),
    Rule(
        "list_windows",
        "list_windows",
        rx(r"\b(?:what|which|list|show)\s+(?:apps|applications|windows|programs)\s+(?:are|is)?\s*(?:open|running)?\b",
           r"^list\s+(?:open\s+)?(?:windows|apps)$"),
        None,
        confidence=0.88,
    ),
    # ---------------- volume / media / brightness ----------------
    Rule(
        "set_volume",
        "set_volume",
        rx(
            r"\b(?:set|make|turn|put)\s+(?:the\s+)?volume\s+(?:to\s+)?(?P<amount>\d{1,3}|ten|twenty|thirty|"
            r"forty|fifty|sixty|seventy|eighty|ninety|hundred|max|full|half)\s*%?\b",
            r"\bvolume\s+(?P<amount2>\d{1,3})\b",
            r"^(?:mute|unmute)\s*(?:the\s+)?(?:sound|audio|volume)?$",
        ),
        lambda m, t: ({"muted": True} if t.startswith("mute")
                      else {"muted": False} if t.startswith("unmute")
                      else _percent_arguments(m, t)),
        confidence=0.92,
    ),
    Rule(
        "media_control",
        "media_control",
        rx(r"\b(?P<action>pause|resume|play|next track|previous track|skip|stop)\s*(?:the\s+)?(?:music|song|video|track|media)?\b"),
        lambda m, t: {"action": (m.group("action") or "play").lower()},
        confidence=0.8,
    ),
    Rule(
        "set_brightness",
        "set_brightness",
        rx(r"\b(?:set|turn|make)\s+(?:the\s+)?(?:screen\s+)?brightness\s+(?:to\s+)?(?P<amount>\d{1,3}|max|full|half|low|high)\s*%?\b",
           r"\b(?:dim|brighten)\s+(?:the\s+)?screen\b"),
        lambda m, t: {"percent": {"low": 20, "high": 90, "dim": 20, "brighten": 90}.get(t.split()[0], None)
                      or _percent_arguments(m, t).get("percent", 60)},
        confidence=0.88,
    ),
    # ---------------- web ----------------
    Rule(
        "search_web",
        "search_web",
        rx(
            r"\b(?:search|google|look up|lookup|bing|duckduckgo|find out|research)\s+(?:the\s+web\s+)?"
            r"(?:for|about|on)?\s*(?P<query>.+)$",
            r"^what\s+(?:is|are|was|were)\s+(?P<query2>.+)\?$",
            r"^who\s+(?:is|was)\s+(?P<query3>.+)\?$",
            r"^how\s+(?:do|does|can|to)\s+(?P<query4>.+)\?$",
            r"^why\s+(?:is|are|does|do)\s+(?P<query5>.+)\?$",
        ),
        lambda m, t: _search_arguments(_remap(m), t),
        confidence=0.84,
    ),
    Rule(
        "open_url",
        "open_url",
        rx(
            r"\b(?:open|go to|goto|navigate to|visit|browse to|load)\s+(?:the\s+)?(?:website|site|page|url)?\s*"
            r"(?P<url>(?:https?://|www\.)[^\s]+|[a-z0-9-]+\.(?:com|org|net|io|dev|ai|co|in|edu|gov|app|me|tech)(?:/[^\s]*)?)\b",
            r"\bopen\s+(?P<url2>localhost:\d+(?:/[^\s]*)?)\b",
        ),
        lambda m, t: _url_arguments(_remap(m, {"url": ("url", "url2")}), t),
        confidence=0.93,
    ),
    Rule(
        "read_page",
        "read_page",
        rx(r"\b(?:read|summarise|summarize|extract|scrape)\s+(?:this|the)?\s*(?:page|article|url|site|website)\b"),
        None,
        confidence=0.85,
    ),
    # ---------------- files ----------------
    Rule(
        "find_file",
        "find_file",
        rx(
            # "find my resume pdf", "locate notes.txt", "where is my screenshot"
            r"\b(?:find|search for|locate|look for|where is|where's)\s+(?:the\s+|my\s+|a\s+|an\s+)?"
            r"(?P<query>[\w.\-* ]{2,60}?)[\"”']?\s*$",
            # "find the file called budget.xlsx"
            r"\b(?:find|search for|locate)\s+(?:the\s+|my\s+)?(?:file|folder|document)s?\s+"
            r"(?:named|called|by the name of)?\s*[\"“']?(?P<query2>[\w.\-* ]{2,60}?)[\"”']?\s*$",
        ),
        _file_query_arguments,
        confidence=0.88,
    ),
    Rule(
        "open_file",
        "open_file",
        rx(r"\bopen\s+(?:the\s+|my\s+)?(?:file|document|folder|project)?\s*[\"“']?(?P<path>[^\s\"”']+\.[a-z0-9]{1,6})[\"”']?\b"),
        lambda m, t: {"path": (m.group("path") or "").strip()},
        confidence=0.86,
    ),
    Rule(
        "list_directory",
        "list_directory",
        rx(r"\b(?:list|show)\s+(?:the\s+)?(?:files|contents|directory|folder)(?:\s+in\s+(?P<path>.+))?$"),
        lambda m, t: {"path": (m.groupdict().get("path") or "").strip()},
        confidence=0.8,
    ),
    Rule(
        "create_file",
        "create_file",
        rx(r"\b(?:create|make|write)\s+(?:a\s+)?(?:new\s+)?(?:file|note)\s+(?:called|named)?\s*[\"“']?(?P<path>[\w./\\ -]+?)[\"”']?"
           r"(?:\s+(?:with|containing|saying)\s+(?P<content>.+))?$"),
        lambda m, t: {"path": (m.group("path") or "").strip(), "content": (m.groupdict().get("content") or "").strip()},
        confidence=0.8,
    ),
    Rule(
        "delete_file",
        "delete_file",
        rx(r"\b(?:delete|remove|trash)\s+(?:the\s+|my\s+)?(?:file|folder)?\s*[\"“']?(?P<path>[\w./\\ -]+?)[\"”']?\s*$"),
        lambda m, t: {"path": (m.group("path") or "").strip()},
        confidence=0.84,
    ),
    # ---------------- system ----------------
    Rule(
        "time",
        "get_time",
        rx(r"^what(?:'s| is)?\s+(?:the\s+)?time\b", r"\btell\s+me\s+the\s+time\b", r"^time\?$",
           r"\bwhat\s+time\s+is\s+it\b"),
        None,
        confidence=0.98,
    ),
    Rule(
        "date",
        "get_date",
        rx(r"^what(?:'s| is)?\s+(?:the\s+)?date\b", r"\bwhat\s+day\s+is\s+it\b", r"^today's date\?$",
           r"\b(?:what(?:'s| is)|tell me|show me)?\s*(?:the\s+)?today'?s\s+date\b",
           r"\bthe\s+date\s+today\b", r"\bwhat\s+is\s+the\s+date\b"),
        None,
        confidence=0.97,
    ),
    Rule(
        "system_status",
        "get_system_status",
        rx(r"\b(?:system|computer|machine|pc)\s+(?:status|health|stats|info)\b",
           r"\bhow(?:'s| is)\s+(?:my|the)\s+(?:computer|pc|system|machine|battery|cpu|memory|ram)\b",
           r"\b(?:battery|cpu|ram|memory|disk)\s+(?:level|status|percentage|percent)\b",
           r"^status\?$"),
        None,
        confidence=0.92,
    ),
    Rule(
        "power",
        "power",
        rx(
            r"\b(?P<action>shut\s?down|shutdown|power\s?off|turn\s?off|restart|reboot|sleep|hibernate|lock)\b"
            r"\s*(?:the\s+|my\s+)?(?:computer|pc|machine|system|screen|laptop)?\s*$",
            r"^(?:lock|sleep|restart|shutdown|shut down|power off)\s*(?:it|now|the computer|my computer)?$",
        ),
        lambda m, t: {"action": _normalise_power(m.groupdict().get("action") or t.split()[0])},
        confidence=0.94,
    ),
    Rule(
        "run_command",
        "run_command",
        rx(r"\b(?:run|execute)\s+(?:the\s+)?(?:command|terminal command|shell command)?\s*[\"“']?(?P<command>[^\"”']+)[\"”']?\s*$"),
        lambda m, t: {"command": (m.group("command") or "").strip()},
        confidence=0.75,
    ),
    # ---------------- productivity ----------------
    Rule(
        "create_reminder",
        "create_reminder",
        rx(
            r"\b(?:remind|reminder|don't let me forget|dont let me forget)\b",
            r"\b(?:set|add|create|make|put|schedule)\s+(?:a\s+|an\s+|the\s+)?reminder\b",
            r"\bnudge\s+me\b",
        ),
        _reminder_arguments,
        confidence=0.95,
    ),
    Rule(
        "list_reminders",
        "list_reminders",
        rx(r"\b(?:what|which|list|show|read out)\b.*\breminders?\b", r"^reminders\?$",
           r"\b(?:my|the)\s+(?:upcoming\s+)?(?:schedule|agenda|reminders)\b"),
        lambda m, t: {"status": "all" if "all" in t else "pending",
                      "limit": 20},
        confidence=0.9,
    ),
    Rule(
        "cancel_reminder",
        "cancel_reminder",
        rx(r"\b(?:cancel|delete|remove|drop|clear)\s+(?:the\s+|my\s+)?(?:reminder|reminders)?\s*(?:about|for|to|that says)?\s*(?P<needle>.*)$"),
        lambda m, t: {"needle": (m.groupdict().get("needle") or "").strip(), "all": "all reminders" in t},
        confidence=0.8,
    ),
    Rule(
        "complete_reminder",
        "complete_reminder",
        rx(r"\b(?:i\s+(?:already\s+)?|mark\s+|set\s+)?(?:did|done|finished|completed)\s+(?:the\s+|my\s+)?(?P<needle>.+?)\s+reminder\b",
           r"\b(?:complete|finish|done with)\s+(?:the\s+)?reminder\s+(?:about|for)?\s*(?P<needle2>.*)$"),
        lambda m, t: {"needle": (m.group("needle") or m.group("needle2") or "").strip()},
        confidence=0.83,
    ),
    Rule(
        "snooze_reminder",
        "snooze_reminder",
        rx(r"\b(?:snooze|postpone|delay|push)\s+(?:it|that|the reminder|my reminder)?\s*(?:for|by)?\s*(?P<amount>\d+\s*(?:minutes|mins|hours))?\b"),
        lambda m, t: {"minutes": 10 if not m.groupdict().get("amount")
                      else int(re.sub(r"\D", "", m.group("amount").split()[0])) *
                           (60 if "hour" in m.group("amount") else 1)},
        confidence=0.72,
    ),
    Rule(
        "create_event",
        "create_calendar_event",
        rx(
            r"\b(?:add|create|schedule|book|set up|put|make)\s+(?:a\s+|an\s+|the\s+)?(?:calendar\s+)?"
            r"(?:event|meeting|appointment|call|slot|entry)\b",
            r"\b(?:schedule|book)\s+(?:a\s+)?(?:meeting|call|appointment)\b",
        ),
        _event_arguments,
        confidence=0.9,
    ),
    Rule(
        "list_events",
        "list_calendar_events",
        rx(r"\b(?:what|show|list)\b.*\b(?:on my calendar|calendar|schedule|meetings|agenda|events)\b.*\b(?:today|tomorrow|this week|week|month)?\b",
           r"^calendar\?$"),
        lambda m, t: {"window": _calendar_window(t)},
        confidence=0.8,
    ),
    Rule(
        "create_task",
        "create_task",
        rx(r"\b(?:add|create|make|new)\s+(?:a\s+)?(?:task|todo|to-do|to do)\b(?:\s*(?:to|:|for)?\s*(?P<title>.+))?$"),
        lambda m, t: {"title": (m.groupdict().get("title") or "").strip()},
        confidence=0.86,
    ),
    Rule(
        "list_tasks",
        "list_tasks",
        rx(r"\b(?:what|show|list|read)\b.*\b(?:tasks|todos|to-dos|to do list|todo list)\b", r"^tasks\?$"),
        None,
        confidence=0.88,
    ),
    Rule(
        "complete_task",
        "complete_task",
        rx(r"\b(?:complete|finish|mark(?:\s+\w+)?\s+(?:as\s+)?done|done with|check off)\s+(?:the\s+)?(?:task\s+)?[\"“']?(?P<needle>.+?)[\"”']?\s*$"),
        lambda m, t: {"needle": (m.group("needle") or "").strip()},
        confidence=0.8,
    ),
    # ---------------- memory ----------------
    Rule(
        "remember",
        "remember_fact",
        rx(
            r"\b(?:remember|note|make a note|keep in mind|store)\s+(?:that\s+)?(?P<fact>.+)$",
            r"\bmy\s+(?P<key2>name|email|phone|birthday|project|favourite color|favorite colour)\s+is\s+(?P<value2>.+)$",
        ),
        lambda m, t: _fact_arguments(m, t),
        confidence=0.8,
    ),
    Rule(
        "recall",
        "recall_fact",
        rx(r"\b(?:what|who) (?:do you know|is my|are my)\b.*\b(?P<topic>name|email|phone|birthday|project|projects|goals?|preferences?|favourite|favorite)\b.*\??$",
           r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\s+(?:me|my)\s*(?P<topic2>.*)\??$"),
        lambda m, t: {"query": (m.groupdict().get("topic") or m.groupdict().get("topic2") or "me").strip()},
        confidence=0.85,
    ),
    Rule(
        "forget",
        "forget_fact",
        rx(r"\bforget\s+(?:that\s+|about\s+)?(?P<key>.+)$"),
        lambda m, t: {"query": (m.group("key") or "").strip()},
        confidence=0.78,
    ),
    # ---------------- workflows / meta ----------------
    Rule(
        "workflow",
        "run_workflow",
        rx(
            r"\b(?:prepare|set up|setup|start|get ready|ready)\s+(?:my\s+|the\s+)?(?:workspace|environment|desk|"
            r"coding setup|dev setup|morning routine|study setup)\b",
            r"\brun\s+(?:the\s+)?(?:workflow|routine|macro)\s+(?P<name>.+)$",
        ),
        lambda m, t: {"name": (m.groupdict().get("name") or _guess_workflow(t)).strip()},
        confidence=0.9,
    ),
    Rule(
        "plan",
        "make_plan",
        rx(r"\b(?:plan|figure out|work out|break down|outline)\s+(?:how\s+)?(?:to|out)?\s*(?P<goal>.{6,})$"),
        lambda m, t: {"goal": (m.group("goal") or t).strip()},
        confidence=0.5,
    ),
    Rule(
        "list_tools",
        "list_tools",
        rx(r"\bwhat\s+can\s+you\s+do\b", r"\b(?:list|show)\s+(?:your\s+)?(?:tools|capabilities|commands)\b",
           r"^help$", r"\bhow\s+do\s+i\s+use\s+you\b"),
        None,
        confidence=0.93,
    ),
    Rule(
        "describe_screen",
        "describe_screen",
        rx(r"\b(?:what|describe|tell me)\b.*\b(?:on|about)\s+(?:my|the)\s+screen\b",
           r"\bwhat\s+(?:am i|can i|do you)\s+(?:looking at|see)\b",
           r"\bdescribe\s+(?:the\s+)?(?:current\s+)?screen\b"),
        None,
        confidence=0.9,
    ),
    # ---------------- small talk ----------------
    Rule(
        "greeting",
        "respond",
        rx(r"^(?:hi|hello|hey|yo|good morning|good afternoon|good evening|namaste)\b"),
        None,
        confidence=0.7,
    ),
    Rule(
        "thanks",
        "respond",
        rx(r"^(?:thanks|thank you|thx|cheers|nice|great|awesome|perfect|cool)\b"),
        None,
        confidence=0.7,
    ),
    Rule(
        "identity",
        "respond",
        rx(r"\bwho\s+are\s+you\b", r"\bwhat\s+are\s+you\b", r"\byour\s+name\b"),
        None,
        confidence=0.8,
    ),
)


def _remap(match: re.Match[str], mapping: dict[str, tuple[str, ...]] | None = None) -> re.Match[str]:
    """Give every named group a canonical alias so builders stay simple."""
    return _AliasMatch(match, mapping or {"query": ("query", "query2", "query3", "query4", "query5"),
                                          "url": ("url", "url2")})


class _AliasMatch:
    """Duck-typed Match object that resolves alias group names."""

    def __init__(self, match: re.Match[str], aliases: dict[str, tuple[str, ...]]) -> None:
        self._match = match
        self._aliases = aliases

    def group(self, key: Any = 0) -> str | None:
        if isinstance(key, str) and key in self._aliases:
            for candidate in self._aliases[key]:
                try:
                    value = self._match.group(candidate)
                except (IndexError, re.error):
                    value = None
                if value:
                    return value
            return None
        try:
            return self._match.group(key)
        except (IndexError, re.error):
            return None

    def groupdict(self) -> dict[str, Any]:
        data = dict(self._match.groupdict())
        for alias, candidates in self._aliases.items():
            for candidate in candidates:
                if data.get(candidate):
                    data[alias] = data[candidate]
                    break
        return data

    def groups(self) -> tuple[Any, ...]:
        return self._match.groups()


def _fact_arguments(match: re.Match[str], text: str) -> dict[str, Any]:
    if match.groupdict().get("key2"):
        return {"key": match.group("key2").strip(), "value": (match.group("value2") or "").strip(),
                "category": "profile"}
    fact = (match.group("fact") or "").strip()
    # "my name is Aprameya" / "I prefer python" / "my project is called X"
    identity = re.match(r"^(?:my|the)\s+(?P<key>[\w ]{2,30}?)\s+(?:is|are)\s+(?P<value>.+)$", fact)
    if identity:
        return {"key": identity.group("key").strip(), "value": _clean_fact_value(identity.group("value")),
                "category": "profile"}
    preference = re.match(r"^(?:i\s+)?(?:prefer|like|love|use|always use)\s+(?P<value>.+)$", fact)
    if preference:
        return {"key": "preference", "value": _clean_fact_value(preference.group("value")),
                "category": "preference"}
    # "my project is called Smart Water Grid"
    called = re.match(r"^(?:my|the|our)\s+(?P<key>[\w ]{2,30}?)\s+(?:is\s+)?(?:called|named)\s+(?P<value>.+)$",
                      fact, flags=re.IGNORECASE)
    if called:
        key = called.group("key").strip().lower()
        key = "current_project" if "project" in key else key
        return {"key": key, "value": _clean_fact_value(called.group("value")), "category": "profile"}
    return {"note": _clean_fact_value(fact), "category": "general"}


def _clean_fact_value(value: str) -> str:
    """Trim connective fluff off a remembered value."""
    cleaned = (value or "").strip()
    cleaned = re.sub(r"^(?:called|named|known as|is|are|=|:)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+(?:and|but|so)\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned.strip(" .,;:\"'") or cleaned


def _normalise_power(action: str) -> str:
    action = action.lower().strip()
    if action in {"shut down", "shutdown", "power off", "turn off"}:
        return "shutdown"
    if action in {"restart", "reboot"}:
        return "restart"
    if action in {"sleep", "hibernate"}:
        return action
    if action == "lock":
        return "lock"
    return action


def _calendar_window(text: str) -> str:
    lowered = text.lower()
    if "today" in lowered:
        return "today"
    if "tomorrow" in lowered:
        return "tomorrow"
    if "week" in lowered:
        return "week"
    if "month" in lowered:
        return "month"
    return "today"


def _guess_workflow(text: str) -> str:
    lowered = text.lower()
    if "study" in lowered or "exam" in lowered:
        return "study_setup"
    if "morning" in lowered:
        return "morning_routine"
    return "coding_setup"


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


class OfflineBrain:
    """Deterministic intent → tool-call engine (no network, no API key)."""

    name = "offline"
    CONFIDENCE_THRESHOLD = 0.5

    def __init__(self, settings: Any, registry: Any = None) -> None:
        self.settings = settings
        self.registry = registry
        self.available = True
        self.rules = RULES
        self.last_error: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": "jarvis-rule-engine",
            "available": True,
            "rules": len(self.rules),
            "note": "Deterministic intent parser. Set OPENAI_API_KEY for open-ended reasoning.",
        }

    # ------------------------------------------------------------------
    def set_registry(self, registry: Any) -> None:
        self.registry = registry

    def _enabled(self, tool: str, allowed: set[str] | None) -> bool:
        if allowed is not None and tool not in allowed:
            return False
        if self.registry is not None:
            spec = self.registry.get(tool)
            if spec is None:
                return False
            return spec.enabled
        return True

    # ------------------------------------------------------------------
    async def decide(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        catalogue: str = "",
        context: dict[str, Any] | None = None,
        **_: Any,
    ) -> Decision:
        allowed: set[str] | None = None
        if tools:
            allowed = {(t.get("function") or {}).get("name", "") for t in tools}

        user_text = ""
        for message in reversed(messages):
            if message.role == "user" and message.content:
                user_text = message.content
                break

        cleaned = _clean(user_text)
        now = (context or {}).get("now") or datetime.now()

        matches: list[tuple[float, Rule, Any]] = []
        for rule in self.rules:
            found, confidence = rule.match(cleaned)
            if not found:
                continue
            if not self._enabled(rule.tool, allowed) and rule.tool not in {"respond"}:
                continue
            matches.append((confidence, rule, found))

        if matches:
            matches.sort(key=lambda item: item[0], reverse=True)
            for confidence, rule, found in matches:
                if confidence < self.CONFIDENCE_THRESHOLD:
                    break
                decision = self._build_decision(rule, found, user_text, cleaned, now, confidence)
                if decision is not None:
                    return decision

        return self._fallback(user_text, cleaned, now, allowed)

    # ------------------------------------------------------------------
    def _build_decision(
        self, rule: Rule, match: Any, user_text: str, cleaned: str, now: datetime, confidence: float
    ) -> Decision | None:
        arguments: dict[str, Any] = {}
        extra_calls: list[ToolCall] = []

        if rule.build is not None:
            try:
                produced = rule.build(match, cleaned)
            except Exception as exc:  # noqa: BLE001
                produced = None
                self.last_error = f"{rule.intent}: {exc}"
            if isinstance(produced, list):
                extra_calls = [c for c in produced if isinstance(c, ToolCall)]
                produced = None
            if isinstance(produced, dict):
                if produced.get("_abort"):
                    # this rule matched the words but not the intent; let a
                    # lower-confidence rule (or the fallback) take over
                    return None
                arguments = {k: v for k, v in produced.items() if not k.startswith("_") or k == "_missing_time"}

        if rule.tool == "respond":
            return Decision(
                text=self._small_talk(rule.intent, user_text),
                intent=rule.intent,
                provider=self.name,
                model="jarvis-rule-engine",
                confidence=confidence,
            )

        human = arguments.pop("_human", None)
        missing = arguments.pop("_missing_time", False)

        # a reminder with no parseable time: ask instead of guessing
        if missing and rule.tool in {"create_reminder", "create_calendar_event"}:
            question = (
                "When should I remind you?"
                if rule.tool == "create_reminder"
                else "When does that start?"
            )
            return Decision(text=question, intent=rule.intent, provider=self.name,
                            model="jarvis-rule-engine", confidence=confidence)

        calls = [ToolCall(name=rule.tool, arguments=arguments, source="offline-intent")] + extra_calls
        notes = f"matched “{rule.intent}”"
        if human:
            notes += f" at {human}"
        return Decision(
            text="",
            tool_calls=calls,
            intent=rule.intent,
            provider=self.name,
            model="jarvis-rule-engine",
            confidence=confidence,
            finish_reason=notes,
        )

    # ------------------------------------------------------------------
    def _fallback(self, user_text: str, cleaned: str, now: datetime, allowed: set[str] | None) -> Decision:
        """Nothing matched: answer, or hand a question to web search."""
        if not cleaned:
            return Decision(text="I didn't catch that.", intent="empty", provider=self.name,
                            model="jarvis-rule-engine", confidence=0.0)

        question = re.search(
            r"\b(?:what|who|when|where|why|how|which)\b.*\?", cleaned
        ) or cleaned.startswith(("what", "who", "when", "where", "why", "how", "which"))

        if question and self._enabled("search_web", allowed) and len(cleaned.split()) > 3:
            query = re.sub(r"^(?:what|who|when|where|why|how|which)\s+(?:is|are|was|were|do|does|did|to|can)?\s*",
                           "", cleaned).strip(" ?")
            return Decision(
                tool_calls=[ToolCall(name="search_web", arguments={"query": query or cleaned, "answer": True},
                                     source="offline-intent")],
                intent="answer_question",
                provider=self.name,
                model="jarvis-rule-engine",
                confidence=0.6,
                text="",
            )

        return Decision(
            text=self._help_text(user_text),
            intent="smalltalk",
            provider=self.name,
            model="jarvis-rule-engine",
            confidence=0.2,
        )

    # ------------------------------------------------------------------
    def _small_talk(self, intent: str, user_text: str) -> str:
        name = self.settings.agent_name
        if intent == "greeting":
            hour = datetime.now().hour
            part = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening"
            return f"Good {part}. {name} is ready — what do you need?"
        if intent == "thanks":
            return "Anytime."
        if intent == "identity":
            return (
                f"I'm {name}, your local agent. I can control apps and the browser, search the web, "
                "manage reminders, calendar and tasks, read and write files, and run multi-step "
                "workflows. Say “what can you do” for the full list."
            )
        return "Sorry?"

    def _help_text(self, user_text: str) -> str:
        provider_hint = "" if getattr(self.settings, "llm_available", False) else (
            " I'm running on the offline rule engine right now — set OPENAI_API_KEY for open-ended reasoning."
        )
        return (
            f"I couldn't map “{user_text[:80]}” to one of my tools.{provider_hint} "
            "Try something like “open VS Code”, “close this tab”, “search the web for FastAPI”, "
            "“remind me tomorrow at 9 to submit my assignment”, “take a screenshot”, or “what can you do”."
        )

    # ------------------------------------------------------------------
    async def plan(self, goal: str, *, catalogue: str, context: str = "", **_: Any) -> dict[str, Any]:
        """Offline planner: recognise known multi-step goals, else admit defeat."""
        from ..automation.workflows import match_workflow

        workflow = match_workflow(goal)
        if workflow:
            return {
                "title": workflow.title,
                "steps": [
                    {"tool": step["tool"], "arguments": step.get("arguments", {}),
                     "why": step.get("why", ""), "critical": step.get("critical", True)}
                    for step in workflow.steps
                ],
                "notes": f"matched built-in workflow `{workflow.name}`",
            }
        return {"title": goal, "steps": [], "notes": "offline planner could not decompose this goal"}

    async def summarise(self, *, user_text: str, observations: list[str], **_: Any) -> str:
        if observations:
            return observations[-1]
        return "Done."


__all__ = ["OfflineBrain", "RULES", "Rule"]
