#!/usr/bin/env bash
set -euo pipefail
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

cd "$(dirname "$0")/.."
DIRS=".venv web/node_modules .cache dist build .pytest_cache .mypy_cache .ruff_cache"
if $DRY_RUN; then
    echo "[DRY-RUN] Would delete:"
    for d in $DIRS; do [ -e "$d" ] && echo "  $d"; done
else
    for d in $DIRS; do
        if [ -e "$d" ]; then
            rm -rf "$d"
            echo "Deleted: $d"
        fi
    done
    echo "Clean done (kept .agentguard/ docs/ src/ .git)"
fi
