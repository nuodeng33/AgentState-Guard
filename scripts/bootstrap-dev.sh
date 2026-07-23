#!/usr/bin/env bash
set -euo pipefail
# AgentState Guard — Development Environment Bootstrap
# Idempotent, --dry-run supported

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

echo "=== AgentState Guard Bootstrap (dry-run=$DRY_RUN) ==="
echo "Python: $(python3 --version 2>&1)"
echo "Node: $(node --version 2>&1)"
echo "npm: $(npm --version 2>&1)"
echo ""

cd "$(dirname "$0")/.."

if $DRY_RUN; then
    echo "[DRY-RUN] Would create: .venv/"
    echo "[DRY-RUN] Would run: .venv/bin/python -m pip install -e \".[dev]\""
    echo "[DRY-RUN] Would create: web/node_modules/"
    echo "[DRY-RUN] Would run: cd web && npm ci"
    exit 0
fi

# Python venv
if [ ! -d .venv ]; then
    echo "Creating .venv..."
    python3 -m venv .venv
fi
echo "Installing Python deps..."
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -e ".[dev]" -q

# Node deps
if [ -f web/package.json ] && [ ! -d web/node_modules ]; then
    echo "Installing Node deps..."
    cd web
    if [ -f package-lock.json ]; then
        npm ci --quiet
    else
        npm install --package-lock-only
    fi
    cd ..
fi

echo ""
echo "=== Bootstrap complete ==="
echo "Run: .venv/bin/python -m agentguard doctor"
echo "Run: .venv/bin/python -m pytest tests/"
