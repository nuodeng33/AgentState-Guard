# GitHub Release Checklist

1. All 139+ tests pass on Linux/Windows/macOS
2. Frontend: npm ci, typecheck, test, build
3. Wheel contains web_static/ assets
4. Fresh venv install: agentguard --help, doctor, ui
5. GUI HTTP smoke: /, /api/health, static assets, SPA fallback
6. GitHub Actions CI passes (3 OS × 3 Python)
7. CodeQL, Dependency Review, Scorecard pass
8. Release workflow on tag creates:
   - Wheel + sdist
   - SHA-256 checksums
   - SBOM (CycloneDX)
   - Build provenance
