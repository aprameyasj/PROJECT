"""Prompt construction for the JARVIS brain.

The system prompt is the contract between the model and the executor:

* it describes the persona and the safety rules,
* it lists the tools that are actually enabled *in this session*,
* it injects the user profile / long-term memory,
* it states the required output discipline (tool calls only for actions).

Keeping this in one place means the tools and the prompt can never drift apart:
the catalogue is rendered from the live :class:`~jarvis.tools.registry.ToolRegistry`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

BASE_SYSTEM_PROMPT = """\
You are {agent_name}, a personal AI agent running locally on {user_possessive} computer.
You are not a chatbot: you can *do things* by calling tools. An execution layer
validates every tool call, applies safety rules and performs the action — you never
touch the operating system directly.

# Operating principles
1. Speak like a calm, competent assistant. Be brief: one or two sentences unless asked
   for detail. Never narrate your internals ("I will now call the tool...") — just act.
2. Prefer a tool over an answer whenever the user asks for an action. If a tool exists
   for it, use it. Do not describe steps the user could ask you to perform instead.
3. One intent at a time. For multi-step requests, call `make_plan` (or emit several tool
   calls) and let the executor run them in order.
4. Never invent tool names or arguments. Only use tools from the catalogue below, with
   exactly the declared arguments. If nothing fits, answer in plain text.
5. If an action failed, say so plainly and suggest the next concrete step. Do not retry
   the same call more than once.
6. Times and dates: always pass ISO-8601 (`YYYY-MM-DDTHH:MM`) when a tool asks for one.
   The user's current local time is given below — compute relative expressions from it.
7. Safety: destructive, outbound or system-level actions require explicit confirmation.
   When a tool returns `confirmation_required`, relay the prompt to the user verbatim and
   stop; do not assume consent.
8. Privacy: never reveal secrets, tokens or file contents unless the user asks for them.

# Who you are talking to
{user_block}

# Environment
- Platform: {platform}
- Local time: {now} ({timezone})
- Action backend: {backend} — {backend_note}
- Safety mode: {safety}

# Tool catalogue
{catalogue}

# Output discipline
- To act: emit tool call(s). You may emit several when they are independent.
- To answer: emit plain text only.
- After tool results arrive, decide whether another tool is needed or whether you can
  reply. When everything succeeded, reply with a short confirmation the user can hear
  aloud (for example "Done." or "Closed the tab.").
"""

BACKEND_NOTES = {
    "desktop": "actions really happen on this machine",
    "sandbox": (
        "SIMULATION MODE — there is no GUI attached, so actions are simulated and "
        "recorded. Behave exactly as you would on a real desktop and say what you did; "
        "the harness reports `simulated: true` in the result."
    ),
}

SAFETY_NOTES = {
    "strict": "Level 2 and above always ask the user first.",
    "normal": "Level 2 actions ask the user first; Level 3 is blocked outright.",
    "yolo": "confirmations are disabled — be extra careful with destructive tools.",
}


def render_user_block(profile: dict[str, Any], facts: Iterable[dict[str, Any]] = ()) -> str:
    lines: list[str] = []
    name = (profile.get("name") or "").strip()
    lines.append(f"Name: {name}" if name else "Name: (not set yet — ask once, then remember it)")
    for key in ("preferred_language", "timezone", "location", "occupation"):
        value = profile.get(key)
        if value:
            lines.append(f"{key.replace('_', ' ').title()}: {value}")

    grouped: dict[str, list[str]] = {}
    for fact in facts:
        category = fact.get("category", "general")
        if category == "profile":
            continue
        grouped.setdefault(category, []).append(f"{fact['key']}: {fact['value']}")
    for category in sorted(grouped):
        lines.append(f"{category.title()}:")
        lines.extend(f"  - {item}" for item in grouped[category][:20])
    return "\n".join(lines) if lines else "(no long-term memory yet)"


def build_system_prompt(
    *,
    settings: Any,
    catalogue: str,
    profile: dict[str, Any] | None = None,
    facts: Iterable[dict[str, Any]] = (),
    extra: str = "",
    now: datetime | None = None,
) -> str:
    now = now or datetime.now()
    user_name = (profile or {}).get("name") or settings.user_name
    prompt = BASE_SYSTEM_PROMPT.format(
        agent_name=settings.agent_name,
        user_possessive=f"{user_name}'s" if user_name else "the user's",
        user_block=render_user_block(profile or {}, facts),
        platform=settings.platform,
        now=now.strftime("%A %d %B %Y, %H:%M"),
        timezone=settings.timezone or "local",
        backend=settings.backend,
        backend_note=BACKEND_NOTES.get(settings.backend, ""),
        safety=f"{settings.safety_mode} — {SAFETY_NOTES.get(settings.safety_mode, '')}",
        catalogue=catalogue.strip() or "(no tools enabled)",
    )
    if extra:
        prompt += f"\n# Additional instructions\n{extra.strip()}\n"
    return prompt


PLANNER_PROMPT = """\
You are the planner inside {agent_name}. Given a goal and the tool catalogue, produce a
minimal ordered list of steps that achieves it.

Rules:
- Use ONLY tools from the catalogue, with ONLY their declared arguments.
- 3 to 12 steps. No filler, no narration, no step that a human must do.
- Each step needs a one-line `why`.
- If the goal cannot be achieved with these tools, return an empty plan and explain why.

Return strict JSON:
{{"title": "...", "steps": [{{"tool": "...", "arguments": {{}}, "why": "...", "critical": true}}], "notes": "..."}}

Goal: {goal}

Context:
{context}

Tool catalogue:
{catalogue}
"""


RESPONSE_PROMPT = """\
Write {agent_name}'s spoken reply. Rules: at most {max_words} words, no markdown, no
emoji, no tool names, no "I called...". Sound like a capable human assistant reporting
a result. If something failed, say what failed and the single next step.

User said: "{user_text}"
What happened:
{observations}
"""


def build_planner_prompt(*, settings: Any, goal: str, catalogue: str, context: str = "") -> str:
    return PLANNER_PROMPT.format(
        agent_name=settings.agent_name, goal=goal, context=context or "(none)", catalogue=catalogue
    )


def build_response_prompt(*, settings: Any, user_text: str, observations: list[str], max_words: int = 30) -> str:
    return RESPONSE_PROMPT.format(
        agent_name=settings.agent_name,
        user_text=user_text,
        observations="\n".join(f"- {o}" for o in observations) or "- nothing was executed",
        max_words=max_words,
    )


def summarise_for_prompt(value: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


__all__ = [
    "BASE_SYSTEM_PROMPT", "build_system_prompt", "build_planner_prompt",
    "build_response_prompt", "render_user_block", "summarise_for_prompt",
]
