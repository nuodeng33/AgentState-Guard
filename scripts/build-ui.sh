#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../web"
echo "Building UI..."
npm ci --quiet 2>/dev/null || npm install --quiet
npm run build 2>&1
echo "UI built: ../agentguard/web_static/"
