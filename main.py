#!/usr/bin/env python3
"""JARVIS — personal AI agent.

    python main.py doctor     # what will run on this machine
    python main.py demo       # the MVP command list, end to end
    python main.py repl       # interactive console
    python main.py serve      # REST + WebSocket + React dashboard
    python main.py ask "open vscode"

See README.md for the architecture and the phase-by-phase roadmap.
"""

from __future__ import annotations

import sys
from pathlib import Path

# allow running from a checkout without installation
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.cli.commands import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
