#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Building AgentState Guard release..."
cd web && npm ci --quiet 2>/dev/null || npm install --quiet && npm run build 2>&1 | tail -2
cd ..
python3 -m build 2>&1 | tail -3
echo "Release artifacts: dist/"
ls dist/
