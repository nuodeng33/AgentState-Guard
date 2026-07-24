# Releasing — AgentState Guard

1. Ensure all tests pass: `python -m pytest tests/`
2. Build frontend: `cd web && npm ci && npm run build`
3. Build wheel: `python -m build`
4. Install from wheel in fresh venv: `pip install dist/*.whl`
5. Smoke test: `agentguard --help && agentguard doctor`
6. Tag release: `git tag v0.9.0.dev0`
7. Push tag to GitHub
8. Create GitHub Release with wheel + sdist + checksums
