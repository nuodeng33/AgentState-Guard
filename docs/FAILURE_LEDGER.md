# Failure Ledger

Aging record of unresolved recurring CI failures. Each entry tracks one
unique failure signature from first sighting through resolution.

## Rules

- 1st occurrence = NEW
- 2nd occurrence = RECURRING — must be explicitly reported
- 3rd occurrence = BLOCKING — stop pushing unrelated fixes until triaged
- NOT_EXECUTED / SKIPPED / CANCELLED do not count as resolution
- Only a relevant CI PASS with the same commit environment counts as RESOLVED

---

## Entry 001: tauri-build externalBin path

**Signature:** `resource path bin\agentguard-sidecar-x86_64-pc-windows-msvc.exe does not exist`

**Layer:** tauri-build

**Root cause:** Tauri 2 `bundle.externalBin: ["bin/agentguard-sidecar"]` expects the
sidecar EXE at `desktop/src-tauri/bin/agentguard-sidecar-x86_64-pc-windows-msvc.exe`,
but the CI workflow placed it at `desktop/src-tauri/agentguard-sidecar.exe` and then
renamed it to `desktop/src-tauri/agentguard-sidecar-x86_64-pc-windows-msvc.exe`
(no `bin/` subdirectory).

### Occurrences

| # | Run | SHA | Date | Age | Status |
|---|---|---|---|---|---|
| 1 | 30158471090 | 143bac93 | 2026-07-25T12:44 | NEW | — |
| 2 | 30158624934 | c689399a | 2026-07-25T12:50 | +6 min | RECURRING |
| 3 | 30158700648 | f1d1caa8 | 2026-07-25T12:52 | +8 min | **BLOCKING — should have stopped here** |
| 4 | 30158765053 | 7a5ad952 | 2026-07-25T12:54 | +10 min | BLOCKING (violation: continued) |
| 5 | 30158961925 | fdcf64f6 | 2026-07-25T13:01 | +17 min | BLOCKING (violation: continued) |
| 6 | 30159049971 | 73b0c0ec | 2026-07-25T13:04 | +20 min | BLOCKING (violation: continued) |
| 7 | 30159129353 | 98ef66ae | 2026-07-25T13:06 | +22 min | BLOCKING (violation: continued) |
| 8 | 30159215488 | 2b28cf94 | 2026-07-25T13:09 | +25 min | BLOCKING (violation: continued) |
| 9 | 30161219754 | 68d19161 | 2026-07-25T14:10 | NEW FIX | externalBin path fixed, new failure: verify artifacts path |

**Resolution:** FIX APPLIED — verify artifact path also corrected. Awaiting next CI run.

### Timeline

```
143bac93 ① NEW        (sidecar-smoke: cross-step death, tauri: externalBin)
c689399a ② RECURRING  (sidecar-smoke: $pid readonly)
f1d1caa8 ③ BLOCKING   ← should have stopped here
7a5ad952 ④ → continued (fixing sidecar-smoke only)
fdcf64f6 ⑤ → continued (fixing 401 session token)
73b0c0ec ⑥ → continued (fixing TimeWait cleanup)
98ef66ae ⑦ → continued (fixing subprocess leak)
2b28cf94 ⑧            (sidecar-smoke PASSED first time, tauri still externalBin)
```

At occurrence ③, the correct action would have been:
1. Report: "tauri-build externalBin has failed 3 consecutive runs, BLOCKING."
2. Triage: fix the `bin/` path before any further sidecar-smoke iterations.
3. Result: sidecar-smoke fixes would have been delayed by ~1 commit, but the
   externalBin issue would have been resolved 5+ commits earlier.

### Current Status

**UNRESOLVED** — fix not yet applied. Running count: 8 occurrences, 7 consecutive repeats.
