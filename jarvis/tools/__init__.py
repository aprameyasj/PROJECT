"""The JARVIS toolset.

Importing this package does not register anything: call
:func:`jarvis.tools.registry.load_tools` with a registry, or let
:class:`jarvis.core.agent.Jarvis` do it for you.
"""

from . import (
    browser,
    calendar_tools,
    communication,
    computer,
    files,
    memory_tools,
    meta,
    reminders,
    system,
    vision,
    web,
)
from .registry import ToolContext, ToolRegistry, is_tool, load_tools, tool

ALL_MODULES = (computer, browser, web, files, system, reminders, calendar_tools, communication,
               memory_tools, vision, meta)

__all__ = ["ToolRegistry", "ToolContext", "tool", "is_tool", "load_tools", "ALL_MODULES"]
