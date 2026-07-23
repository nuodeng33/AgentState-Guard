#!/usr/bin/env bash
set -euo pipefail
echo "=== AgentState Guard Dev Doctor ==="
cd "$(dirname "$0")/.."
echo "pwd: $(pwd)"
echo "Python: $(python3 --version 2>&1)"
echo "Node: $(node --version 2>&1)"
echo ".venv: $([ -d .venv ] && echo 'exists' || echo 'missing')"
echo "web/node_modules: $([ -d web/node_modules ] && echo 'exists' || echo 'missing')"
echo "Git branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'N/A')"
echo "Git HEAD: $(git rev-parse HEAD 2>/dev/null || echo 'N/A')"
echo "Tests: $(python3 -m pytest tests/ -q 2>&1 | tail -1)"
