"""Thin PyInstaller entry point for AgentState Guard desktop sidecar.

Packaged as agentguard-sidecar.exe by PyInstaller.
Launched by Tauri as an external binary (sidecar).

Calls agentguard.cli:main() which dispatches to the serve subcommand
based on CLI arguments passed by the Tauri sidecar manager.

Usage (standalone):
    agentguard-sidecar.exe --directory <data_dir> serve --host 127.0.0.1 --port 8787
"""

import sys
from pathlib import Path

# Ensure the bundled package root is on sys.path when running as PyInstaller EXE
if getattr(sys, "frozen", False):
    _root = Path(sys._MEIPASS)
    pkg_dir = _root / "agentguard"
    if pkg_dir.is_dir():
        sys.path.insert(0, str(_root))

from agentguard.cli import main

if __name__ == "__main__":
    main()
