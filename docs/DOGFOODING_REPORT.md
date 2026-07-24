# Dogfooding Report — AgentState Guard 0.9.0.dev0

| Command | Status | Evidence |
|---------|--------|----------|
| status | VERIFIED_REAL | Prints container detection, versions, port checks |
| doctor | VERIFIED_REAL | PASS/WARN/FAIL/SKIP/UNREACHABLE all render |
| checkpoint | VERIFIED_TEMP_SANDBOX | Creates checkpoint with file hashes |
| checkpoints | VERIFIED_TEMP_SANDBOX | Lists all checkpoints from DB |
| verify | VERIFIED_TEMP_SANDBOX | DB integrity + schema version |
| handoff | VERIFIED_TEMP_SANDBOX | MD + JSON produced |
| incident | VERIFIED_TEMP_SANDBOX | Bundle with checkpoint list |
| host-import | VERIFIED_TEMP_SANDBOX | Stdin JSON, field whitelist enforced |
| report | VERIFIED_TEMP_SANDBOX | MD + JSON in reports/ |
| gc --dry-run | VERIFIED_TEMP_SANDBOX | Plan output, no errors |
| diff | PARTIAL | Config changes detected; file changes limited to tracked paths |
| plan | PARTIAL | Creates transactions but no step detail |
| transactions | PARTIAL | Lists exist but may be empty |
| undo | PARTIAL | State machine tested; needs populated steps |
| test-restore | PARTIAL | Lists restorable files from checkpoint |
| host-probe | STUB | Aliases to host-import |
| update-state | PARTIAL | Creates docs from structured data |
| ui/serve | HTTP_SMOKE_VERIFIED | All endpoints return correct status/content |
