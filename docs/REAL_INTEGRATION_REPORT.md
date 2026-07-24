# Real Integration Report — AgentState Guard 0.9.0.dev0

## Summary
Tested in isolated temp sandbox with real checkpoint, diff, verify, handoff, incident, and GC dry-run. Frontend builds and GUI serves all endpoints correctly.

## Results

| Scenario | Status |
|----------|--------|
| Temp dir isolation | ✅ Files created/modified/deleted in sandbox |
| Checkpoint creation | ✅ Captured tracked files with hashes |
| Diff detection | ✅ Config changes detected |
| Verify (DB integrity) | ✅ All checks pass |
| Handoff generation | ✅ MD + JSON produced |
| Incident bundle | ✅ Sanitized, no secrets |
| GC dry-run | ✅ No errors |
| Frontend npm ci | ✅ Clean install, 0 vulns |
| Frontend typecheck | ✅ tsc --noEmit exit 0 |
| Frontend build | ✅ 414ms, web_static/ populated |
| GUI: homepage | ✅ 200 OK, 430B |
| GUI: JS assets | ✅ 200 OK, 146KB JS |
| GUI: API health | ✅ {"status":"ok"} |
| GUI: Session auth | ✅ Token-based auth |
| GUI: Status (auth'd) | ✅ Full JSON diagnostics |
| GUI: SPA fallback | ✅ Deep links return HTML |
| GUI: API 404 = JSON | ✅ 401 JSON, not HTML |

## Limitations
- File diff detection limited to tracked paths (~/.claude/settings.json, project config)
- test-restore, host-probe remain STUB (only entry points)
- export/import, watch, run are NOT_IMPLEMENTED
