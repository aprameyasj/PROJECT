"""JARVIS — a personal AI agent.

    You speak -> JARVIS understands intent -> picks a tool -> the execution
    layer performs it on your computer/services -> JARVIS reports the result.

The LLM never touches the machine directly: it can only name tools from a
controlled catalogue, and a separate safety + execution layer decides whether
each call may run.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
