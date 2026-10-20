#!/usr/bin/env python3
"""Regenerate docs/TOOLS.md straight from the live tool registry.

    python scripts/export_tools.py

The catalogue is generated rather than hand-written so the documentation can
never drift from the schemas the model is actually given.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.core.agent import Jarvis  # noqa: E402

LEVEL = {"safe": "L1", "confirm": "L2", "forbidden": "L3"}


def render_type(schema: dict) -> str:
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" for v in schema["enum"])
    base = schema.get("type", "string")
    if base == "array":
        return f"array<{render_type(schema.get('items', {}))}>"
    if base == "object":
        return "object"
    if schema.get("nullable"):
        return f"{base} \\| null"
    return base


def main() -> int:
    jarvis = Jarvis()
    specs = jarvis.registry.all(include_disabled=True)

    by_category: dict[str, list] = defaultdict(list)
    for spec in specs:
        by_category[spec.category.value].append(spec)

    lines: list[str] = [
        "# Tool catalogue",
        "",
        "> Generated from the live registry by `python scripts/export_tools.py`.",
        "> The JSON schema sent to the model is inferred from each function's signature and",
        "> docstring — there is no separate schema file to keep in sync.",
        "",
        f"**{len(specs)} tools in {len(by_category)} categories.** `L1` runs immediately,",
        "`L2` asks for confirmation first, `L3` does not exist in the catalogue at all.",
        "",
        "Every tool receives a `ToolContext` (settings, action backend, database, memory,",
        "event bus, scheduler, browser session, session id, the user's utterance) that is",
        "**never** exposed in the schema, and returns a `ToolResult`:",
        "",
        "```python",
        "@dataclass",
        "class ToolResult:",
        "    ok: bool                 # did it work",
        "    message: str             # what happened, for logs and the console",
        "    speech: str              # what JARVIS says out loud",
        "    data: dict               # structured payload for the model / dashboard",
        "    artifacts: list[dict]    # screenshots, files, audio ({type, path, url})",
        "    error: str | None        # set when ok is False",
        "    duration_ms: int",
        "```",
        "",
    ]

    for category in sorted(by_category, key=lambda c: (-len(by_category[c]), c)):
        group = sorted(by_category[category], key=lambda s: s.name)
        lines += [
            "---",
            "",
            f"## {category} · {len(group)} tools",
            "",
        ]
        for spec in group:
            props = spec.parameters.get("properties", {})
            required = set(spec.required)
            signature = ", ".join(
                f"**{name}**" if name in required else f"{name}?" for name in props
            )
            lines += [
                f"### `{spec.name}` {LEVEL.get(spec.permission.value, '?')}",
                "",
                spec.description.strip(),
                "",
                f"`{spec.name}({signature})`",
                "",
            ]
            if props:
                lines += ["| argument | type | notes |", "|---|---|---|"]
                for name, schema in props.items():
                    notes = str(schema.get("description", "")).replace("\n", " ").replace("|", "\\|")
                    if name in required:
                        notes = "**required**" + (f" — {notes}" if notes else "")
                    elif "default" in schema and schema["default"] is not None:
                        notes = f"default `{json.dumps(schema['default'])}`" + (f" — {notes}" if notes else "")
                    lines.append(f"| `{name}` | {render_type(schema)} | {notes} |")
                lines.append("")
            if spec.examples:
                lines += ["Say:", ""] + [f"- “{example}”" for example in spec.examples] + [""]
            if spec.needs:
                lines += [f"Requires: {', '.join(f'`{n}`' for n in spec.needs)}", ""]
            if not spec.enabled:
                lines += ["*Disabled in this configuration.*", ""]

    lines += [
        "---",
        "",
        "## Adding a tool",
        "",
        "```python",
        "# jarvis/tools/computer.py",
        "from ..core.types import Permission, ToolCategory, ToolResult",
        "from .registry import ToolContext, tool",
        "",
        "",
        '@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER, examples=["Pin the window"])',
        "def pin_window(ctx: ToolContext, title: str = \"\") -> ToolResult:",
        '    """Pin the focused window so it stays on top.',
        "",
        "    Args:",
        "        title: Optional window title to pin (defaults to the focused window).",
        '    """',
        '    message = ctx.backend.pin_window(title)          # the backend does the work',
        "    return ToolResult(ok=True, message=message, speech=\"Pinned.\",",
        '                      data={"title": title})',
        "```",
        "",
        "Then add it to the module's loader list in `jarvis/core/agent.py::_load_tools`.",
        "That's the whole integration: the schema, the prompt catalogue, the offline",
        "intent rules, the safety gate, the audit log, the dashboard's Tools tab and the",
        "REST endpoint all pick it up automatically.",
        "",
        "Rules worth following:",
        "",
        "1. **Prefer a specific tool over a general one.** `close_current_tab()` beats",
        "   `run_command(\"xdotool key ctrl+w\")`.",
        "2. **Never shell out to something destructive.** The gate will block it anyway,",
        "   and the audit log will show why.",
        "3. **Return a `ToolResult`, never raise.** The registry catches exceptions and",
        "   turns them into failed observations the model can react to.",
        "4. **Say something speakable.** `speech` is what the user hears; `message` is",
        "   what the console shows.",
        "5. **Declare the honest permission level.** If it leaves the machine or can't be",
        "   undone, that's `Permission.CONFIRM`.",
        "",
    ]

    out = ROOT / "docs" / "TOOLS.md"
    out.write_text("\n".join(lines))
    jarvis.close()
    print(f"wrote {out} ({len(specs)} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
