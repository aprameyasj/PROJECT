"""Tool registry — the *only* surface the model is allowed to touch.

Each tool is a normal Python function decorated with :func:`tool`. The
registry inspects the signature and docstring to build the JSON schema that is
sent to the LLM, so the schema can never drift away from the implementation.

    @tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
    def close_current_tab(ctx: ToolContext) -> ToolResult:
        '''Close the currently focused browser tab.'''
        ctx.backend.hotkey("ctrl", "w")
        return ToolResult(ok=True, message="Closed the active tab.", speech="Done.")
"""

from __future__ import annotations

import inspect
import json
import re
import sys
import time
import types
import typing
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Literal, get_args, get_origin

from ..core.types import Permission, ToolCategory, ToolCall, ToolResult

# ---------------------------------------------------------------------------
# ToolContext — the dependency bag handed to every tool at execution time.
# It is deliberately *not* part of the JSON schema shown to the model.
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    settings: Any = None
    backend: Any = None
    db: Any = None
    memory: Any = None
    events: Any = None
    scheduler: Any = None
    browser: Any = None
    session_id: str = ""
    user_text: str = ""
    #: extra runtime services registered by the host process
    services: dict[str, Any] = field(default_factory=dict)

    def service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)


CTX_TYPES = (ToolContext,)


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
    Any: "string",
}


def _resolve_hints(handler: Callable[..., Any]) -> dict[str, Any]:
    """Resolve stringified annotations (PEP 563) back into real types.

    Every tool module uses `from __future__ import annotations`, so without this
    `int`/`bool`/`Literal[...]` would arrive as the *strings* "int"/"bool" and
    the model would be told every argument is a string.
    """
    try:
        module = sys.modules.get(getattr(handler, "__module__", ""), None)
        globalns = getattr(module, "__dict__", None)
        return typing.get_type_hints(handler, globalns=globalns, include_extras=True)
    except Exception:  # pragma: no cover - unresolvable forward references
        try:
            return typing.get_type_hints(handler)
        except Exception:
            return {}


def _json_type(hint: Any) -> dict[str, Any]:
    origin = get_origin(hint)

    if hint is inspect.Parameter.empty or hint is Any:
        return {"type": "string"}

    if origin is Literal:
        values = list(get_args(hint))
        schema: dict[str, Any] = {"enum": values}
        if all(isinstance(v, str) for v in values):
            schema["type"] = "string"
        return schema

    if origin in (list, typing.List, set, tuple):
        args = get_args(hint)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}

    if origin in (dict, typing.Dict):
        args = get_args(hint)
        value = _json_type(args[1]) if len(args) > 1 else {"type": "string"}
        return {"type": "object", "additionalProperties": value}

    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(hint) if a is not type(None)]
        optional = len(args) != len(get_args(hint))
        if len(args) == 1:
            schema = _json_type(args[0])
            if optional:
                schema = dict(schema)
                schema.setdefault("nullable", True)
            return schema
        return {"anyOf": [_json_type(a) for a in args]}

    if hint in _PY_TO_JSON:
        return {"type": _PY_TO_JSON[hint]}

    if inspect.isclass(hint) and issubclass(hint, str):
        return {"type": "string"}

    if inspect.isclass(hint) and issubclass(hint, bool):
        return {"type": "boolean"}

    if inspect.isclass(hint) and issubclass(hint, datetime):
        return {"type": "string", "format": "date-time"}

    if inspect.isclass(hint) and issubclass(hint, date):
        return {"type": "string", "format": "date"}

    return {"type": "string"}


_DOCSTRING_ARGS_RE = re.compile(r"^\s{2,}(\w+)\s*(\([^)]*\))?\s*:\s*(.+)$")


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (summary+body, {arg: description})."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    summary_lines: list[str] = []
    arg_docs: dict[str, str] = {}
    section: str | None = None
    current_arg: str | None = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if lower.rstrip(":") in {"args", "arguments", "parameters", "params"}:
            section = "args"
            continue
        if lower.rstrip(":") in {"returns", "return", "raises", "example", "examples", "note", "notes", "yields"}:
            section = lower.rstrip(":")
            current_arg = None
            continue

        if section == "args":
            match = _DOCSTRING_ARGS_RE.match(line)
            if match:
                current_arg = match.group(1)
                arg_docs[current_arg] = match.group(3).strip()
            elif current_arg and stripped:
                arg_docs[current_arg] += " " + stripped
            continue

        if section in {None}:
            summary_lines.append(line)
        elif section in {"example", "examples", "note", "notes"}:
            summary_lines.append(line)

    return "\n".join(summary_lines).strip(), arg_docs


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    permission: Permission = Permission.SAFE
    category: ToolCategory = ToolCategory.META
    parameters: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    #: short human sentence used when confirming / reporting
    examples: list[str] = field(default_factory=list)
    #: tools can be turned off without deleting them
    enabled: bool = True
    #: True when the tool mutates state (used by the planner for retries)
    mutating: bool = False
    #: backends this tool needs; used by `doctor` and the dashboard
    needs: tuple[str, ...] = ()
    signature: inspect.Signature | None = None

    # ------------------------------------------------------------------
    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "category": self.category.value,
            "parameters": self.parameters,
            "required": self.required,
            "examples": self.examples,
            "enabled": self.enabled,
            "mutating": self.mutating,
            "needs": list(self.needs),
        }

    def to_catalogue_entry(self) -> str:
        """Compact text form for prompts / offline engine."""
        args = ", ".join(self.parameters.get("properties", {}).keys())
        return f"{self.name}({args}) — {self.description.splitlines()[0] if self.description else ''}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Holds every tool JARVIS knows about and dispatches calls to them."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._stats: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    def register(
        self,
        handler: Callable[..., Any],
        *,
        name: str | None = None,
        permission: Permission | None = None,
        category: ToolCategory | None = None,
        description: str | None = None,
        examples: Iterable[str] | None = None,
        mutating: bool | None = None,
        needs: Iterable[str] | None = None,
        enabled: bool | None = None,
        override: bool = False,
    ) -> ToolSpec:
        """Register `handler` as a tool.

        Anything not passed explicitly falls back to the metadata attached by
        :func:`tool`, then to safe defaults. The JSON schema is inferred from the
        *resolved* signature, so modules using `from __future__ import
        annotations` still advertise real types (int/bool/enum) to the model.
        """
        meta: dict[str, Any] = dict(getattr(handler, "__jarvis_tool__", {}) or {})
        tool_name = name or meta.get("name") or handler.__name__
        if tool_name in self._tools and not override:
            raise ValueError(f"Tool `{tool_name}` is already registered")
        if not re.match(r"^[a-z][a-z0-9_]{1,63}$", tool_name):
            raise ValueError(f"Invalid tool name `{tool_name}` (snake_case, <=64 chars)")

        permission = permission if permission is not None else meta.get("permission", Permission.SAFE)
        category = category if category is not None else meta.get("category", ToolCategory.META)
        examples = list(examples) if examples is not None else list(meta.get("examples", ()))
        mutating = mutating if mutating is not None else bool(meta.get("mutating", False))
        needs = tuple(needs) if needs is not None else tuple(meta.get("needs", ()))
        enabled = enabled if enabled is not None else bool(meta.get("enabled", True))
        description = description or meta.get("description")

        sig = inspect.signature(handler)
        doc_summary, arg_docs = _parse_docstring(handler.__doc__)
        final_description = description or doc_summary or f"Call {tool_name}."

        hints = _resolve_hints(handler)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            annotation = hints.get(pname, param.annotation)
            if annotation in CTX_TYPES or pname in {"ctx", "context", "self"}:
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            schema = _json_type(annotation)
            schema = dict(schema)
            if pname in arg_docs:
                schema["description"] = arg_docs[pname]
            if param.default is not inspect.Parameter.empty:
                if isinstance(param.default, (str, int, float, bool)) or param.default is None:
                    schema["default"] = param.default
            else:
                required.append(pname)
            properties[pname] = schema

        parameters = {"type": "object", "properties": properties, "required": required}

        spec = ToolSpec(
            name=tool_name,
            description=final_description,
            handler=handler,
            permission=permission,
            category=category,
            parameters=parameters,
            required=required,
            examples=list(examples),
            enabled=enabled,
            mutating=mutating,
            needs=tuple(needs),
            signature=sig,
        )
        self._tools[tool_name] = spec
        self._stats.setdefault(tool_name, {"calls": 0, "errors": 0, "total_ms": 0})
        return spec

    # ------------------------------------------------------------------
    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self, *, include_disabled: bool = False) -> list[str]:
        return [
            name
            for name, spec in self._tools.items()
            if spec.enabled or include_disabled
        ]

    def all(self, *, include_disabled: bool = False) -> list[ToolSpec]:
        return [spec for spec in self._tools.values() if spec.enabled or include_disabled]

    def by_category(self) -> dict[str, list[ToolSpec]]:
        grouped: dict[str, list[ToolSpec]] = {}
        for spec in self.all(include_disabled=True):
            grouped.setdefault(spec.category.value, []).append(spec)
        return grouped

    def enable(self, names: Iterable[str]) -> None:
        for name in names:
            if name in self._tools:
                self._tools[name].enabled = True

    def disable(self, names: Iterable[str]) -> None:
        for name in names:
            if name in self._tools:
                self._tools[name].enabled = False

    def restrict_to(self, allowed: Iterable[str]) -> None:
        """Hard-filter the toolset (used for reduced/child-safe modes)."""
        allowed_set = set(allowed)
        for name, spec in self._tools.items():
            spec.enabled = name in allowed_set

    # ------------------------------------------------------------------
    def to_openai_tools(self, subset: Iterable[str] | None = None) -> list[dict[str, Any]]:
        specs = self.all()
        if subset is not None:
            wanted = set(subset)
            specs = [s for s in specs if s.name in wanted]
        return [s.to_openai() for s in specs]

    def prompt_catalogue(self, subset: Iterable[str] | None = None) -> str:
        specs = self.all()
        if subset is not None:
            wanted = set(subset)
            specs = [s for s in specs if s.name in wanted]
        lines: list[str] = []
        for category in sorted({s.category.value for s in specs}):
            lines.append(f"[{category}]")
            for spec in specs:
                if spec.category.value == category:
                    lines.append(f"  - {spec.to_catalogue_entry()}")
        return "\n".join(lines)

    def stats(self) -> dict[str, dict[str, Any]]:
        return {name: dict(data) for name, data in self._stats.items()}

    # ------------------------------------------------------------------
    def validate_arguments(self, spec: ToolSpec, raw: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
        """Light-weight coercion + validation. Returns (args, problems)."""
        args = dict(raw or {})
        problems: list[str] = []
        properties = spec.parameters.get("properties", {})

        # drop unknown keys but keep them reported (models hallucinate extras)
        for key in list(args.keys()):
            if key not in properties:
                problems.append(f"unknown argument `{key}`")
                args.pop(key)

        for key in spec.required:
            if key not in args or args[key] in (None, ""):
                problems.append(f"missing required argument `{key}`")

        for key, value in list(args.items()):
            schema = properties.get(key, {})
            coerced, error = _coerce(value, schema)
            if error:
                problems.append(f"argument `{key}`: {error}")
            else:
                args[key] = coerced

        return args, problems

    # ------------------------------------------------------------------
    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Run a tool. Never raises: everything becomes a ToolResult."""
        spec = self._tools.get(call.name)
        started = time.perf_counter()

        if spec is None:
            return ToolResult(
                ok=False,
                tool=call.name,
                call_id=call.id,
                error=f"Unknown tool `{call.name}`.",
                message=f"I don't have a tool called `{call.name}`.",
                duration_ms=_ms(started),
            )
        if not spec.enabled:
            return ToolResult(
                ok=False,
                tool=call.name,
                call_id=call.id,
                error=f"Tool `{call.name}` is disabled.",
                message=f"`{call.name}` is currently disabled.",
                duration_ms=_ms(started),
            )

        args, problems = self.validate_arguments(spec, call.arguments)
        if problems:
            return ToolResult(
                ok=False,
                tool=call.name,
                call_id=call.id,
                error="; ".join(problems),
                message=f"I couldn't run `{call.name}`: {'; '.join(problems)}",
                duration_ms=_ms(started),
            )

        kwargs = dict(args)
        # inject the context if the handler wants it
        if spec.signature is not None:
            for pname, param in spec.signature.parameters.items():
                if param.annotation in CTX_TYPES or pname in {"ctx", "context"}:
                    kwargs[pname] = ctx

        stats = self._stats.setdefault(spec.name, {"calls": 0, "errors": 0, "total_ms": 0})
        stats["calls"] += 1
        try:
            outcome = spec.handler(**kwargs)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            result = _as_result(outcome, spec.name, call.id)
        except Exception as exc:  # noqa: BLE001 — tools must never crash the agent
            stats["errors"] += 1
            result = ToolResult(
                ok=False,
                tool=spec.name,
                call_id=call.id,
                error=f"{type(exc).__name__}: {exc}",
                message=f"`{spec.name}` failed: {exc}",
            )

        elapsed = _ms(started)
        stats["total_ms"] += elapsed
        result.duration_ms = elapsed
        result.tool = result.tool or spec.name
        result.call_id = result.call_id or call.id
        return result


def _as_result(outcome: Any, tool: str, call_id: str) -> ToolResult:
    if isinstance(outcome, ToolResult):
        outcome.tool = outcome.tool or tool
        outcome.call_id = outcome.call_id or call_id
        return outcome
    if isinstance(outcome, dict) and {"ok"} <= set(outcome):
        return ToolResult(tool=tool, call_id=call_id, **outcome)
    if isinstance(outcome, str):
        return ToolResult(ok=True, tool=tool, call_id=call_id, message=outcome, data=None, speech=outcome)
    if outcome is None:
        return ToolResult(ok=True, tool=tool, call_id=call_id, message="Done.", speech="Done.")
    return ToolResult(ok=True, tool=tool, call_id=call_id, message="Done.", data=_jsonable(outcome))


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _coerce(value: Any, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """Convert model output ("9", "true", "[1,2]") into real Python values."""
    target = schema.get("type")
    enum = schema.get("enum")

    if value is None:
        return None, None

    if target == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            if value.strip().lower() in {"true", "yes", "y", "1", "on"}:
                return True, None
            if value.strip().lower() in {"false", "no", "n", "0", "off"}:
                return False, None
            return value, "expected a boolean"
        return bool(value), None

    if target == "integer":
        try:
            return int(str(value).strip()), None
        except (TypeError, ValueError):
            return value, f"`{value}` is not an integer"

    if target == "number":
        try:
            return float(str(value).strip()), None
        except (TypeError, ValueError):
            return value, f"`{value}` is not a number"

    if target == "array":
        if isinstance(value, list):
            return value, None
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return parsed, None
                except json.JSONDecodeError:
                    pass
            return [p.strip() for p in re.split(r",", text) if p.strip()], None
        return [value], None

    if target == "object":
        if isinstance(value, dict):
            return value, None
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed, None
            except json.JSONDecodeError:
                pass
            return value, "expected a JSON object"
        return value, "expected a JSON object"

    if enum:
        if value in enum:
            return value, None
        if isinstance(value, str):
            lowered = value.strip().lower()
            for option in enum:
                if isinstance(option, str) and option.lower() == lowered:
                    return option, None
        return value, None  # not fatal; the tool can decide

    return value if isinstance(value, str) else _jsonable(value), None


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def tool(
    *,
    name: str | None = None,
    permission: Permission | Literal["safe", "confirm", "forbidden"] = Permission.SAFE,
    category: ToolCategory | str = ToolCategory.META,
    description: str | None = None,
    examples: Iterable[str] = (),
    mutating: bool = False,
    needs: Iterable[str] = (),
    enabled: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach metadata to a function so a registry can pick it up.

    The decorator does *not* register the function globally; module loaders
    call :func:`load_tools` with an explicit registry. That keeps tests
    hermetic and lets the API build different toolsets per session.
    """

    perm = Permission(permission) if isinstance(permission, str) else permission
    cat = ToolCategory(category) if isinstance(category, str) else category

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__jarvis_tool__ = {  # type: ignore[attr-defined]
            "name": name or func.__name__,
            "permission": perm,
            "category": cat,
            "description": description,
            "examples": list(examples),
            "mutating": mutating,
            "needs": tuple(needs),
            "enabled": enabled,
        }
        return func

    return decorator


def is_tool(func: Any) -> bool:
    return callable(func) and hasattr(func, "__jarvis_tool__")


def load_tools(registry: ToolRegistry, module: Any, *, prefix: str = "") -> int:
    """Register every @tool-decorated member of `module`. Returns the count."""
    count = 0
    for _, member in inspect.getmembers(module):
        if not is_tool(member):
            continue
        meta = dict(member.__jarvis_tool__)  # type: ignore[attr-defined]
        meta["name"] = f"{prefix}{meta['name']}" if prefix else meta["name"]
        registry.register(member, override=True, **meta)
        count += 1
    return count
