"""The tool registry is the contract between the model and the machine."""

from __future__ import annotations

from typing import Literal

import pytest

from jarvis.core.types import Permission, ToolCall, ToolCategory, ToolResult
from jarvis.tools.registry import ToolContext, ToolRegistry, load_tools, tool


@tool(permission=Permission.SAFE, category=ToolCategory.COMPUTER)
def demo_tool(ctx: ToolContext, name: str, times: int = 1, mode: Literal["fast", "slow"] = "fast") -> ToolResult:
    """Do a demo thing.

    Args:
        name: The name to use.
        times: How many times to repeat.
        mode: Either fast or slow.
    """
    assert ctx is not None
    return ToolResult(ok=True, message=f"{name} x{times} ({mode})", data={"name": name, "times": times})


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(demo_tool)
    return reg


def test_schema_is_inferred_from_signature_and_docstring(registry: ToolRegistry) -> None:
    spec = registry.get("demo_tool")
    assert spec is not None
    props = spec.parameters["properties"]
    assert "ctx" not in props, "the context must never be exposed to the model"
    assert props["name"]["type"] == "string"
    assert props["name"]["description"] == "The name to use."
    assert props["times"]["type"] == "integer"
    assert props["times"]["default"] == 1
    assert props["mode"]["enum"] == ["fast", "slow"]
    assert spec.required == ["name"]
    assert spec.permission is Permission.SAFE
    assert spec.description.startswith("Do a demo thing.")


def test_openai_payload_shape(registry: ToolRegistry) -> None:
    payload = registry.to_openai_tools()
    assert payload[0]["type"] == "function"
    assert payload[0]["function"]["name"] == "demo_tool"
    assert "parameters" in payload[0]["function"]


def test_catalogue_is_prompt_ready(registry: ToolRegistry) -> None:
    catalogue = registry.prompt_catalogue()
    assert "demo_tool(name, times, mode)" in catalogue
    assert "[computer]" in catalogue


@pytest.mark.asyncio
async def test_execute_coerces_model_output(registry: ToolRegistry) -> None:
    ctx = ToolContext()
    result = await registry.execute(ToolCall(name="demo_tool", arguments={"name": "jarvis", "times": "3"}), ctx)
    assert result.ok
    assert result.data == {"name": "jarvis", "times": 3}
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_execute_rejects_unknown_and_missing_arguments(registry: ToolRegistry) -> None:
    ctx = ToolContext()
    unknown = await registry.execute(
        ToolCall(name="demo_tool", arguments={"name": "x", "bogus": 1}), ctx
    )
    assert unknown.ok is False
    assert "unknown argument" in (unknown.error or "")

    missing = await registry.execute(ToolCall(name="demo_tool", arguments={}), ctx)
    assert missing.ok is False
    assert "missing required argument `name`" in (missing.error or "")


@pytest.mark.asyncio
async def test_execute_never_raises(registry: ToolRegistry) -> None:
    @tool()
    def boom() -> ToolResult:
        raise RuntimeError("kaboom")

    registry.register(boom)
    result = await registry.execute(ToolCall(name="boom", arguments={}), ToolContext())
    assert result.ok is False
    assert "kaboom" in (result.error or "")


@pytest.mark.asyncio
async def test_disabled_tools_refuse_to_run(registry: ToolContext) -> None:
    reg = ToolRegistry()
    reg.register(demo_tool)
    reg.disable(["demo_tool"])
    result = await reg.execute(ToolCall(name="demo_tool", arguments={"name": "x"}), ToolContext())
    assert result.ok is False
    assert "disabled" in (result.error or "")


def test_restrict_to_hard_filters_the_toolset() -> None:
    reg = ToolRegistry()
    reg.register(demo_tool)
    load_tools(reg, __import__("jarvis.tools.computer", fromlist=["computer"]))
    reg.restrict_to(["demo_tool", "close_current_tab"])
    assert set(reg.names()) == {"demo_tool", "close_current_tab"}


def test_every_real_tool_has_valid_metadata() -> None:
    from jarvis.core.agent import Jarvis

    jarvis = Jarvis()
    try:
        specs = jarvis.registry.all(include_disabled=True)
        assert len(specs) >= 40
        for spec in specs:
            assert spec.parameters["type"] == "object"
            assert isinstance(spec.permission, Permission)
            assert spec.description, f"{spec.name} has no description"
            assert spec.name == spec.name.lower()
        # the OpenAI payload must be JSON-serialisable for every tool
        import json

        json.dumps(jarvis.registry.to_openai_tools())
    finally:
        jarvis.close()


def test_tool_names_are_unique_and_snake_case() -> None:
    from jarvis.core.agent import Jarvis

    jarvis = Jarvis()
    try:
        names = jarvis.registry.names(include_disabled=True)
        assert len(names) == len(set(names))
        assert all(name.replace("_", "").isalnum() for name in names)
    finally:
        jarvis.close()
