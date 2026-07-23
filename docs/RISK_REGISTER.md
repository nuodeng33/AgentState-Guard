# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation | Status |
|----|------|-----------|--------|------------|--------|
| R01 | Schema migration fails on production DB | Low | High | Test migration on backup; rollback procedure defined | ACTIVE |
| R02 | Blob store corruption | Low | High | Content-addressed integrity check; GC validation | ACTIVE |
| R03 | Audit chain tampering undetected | Low | Critical | Hash chain with verification | ACTIVE |
| R04 | Unauthorized restore via API | Low | High | CSRF, session auth, path validation | PLANNED |
| R05 | Container lacks Docker socket | Certain | Low | UNREACHABLE status; host-probe as alternative | ACCEPTED |
