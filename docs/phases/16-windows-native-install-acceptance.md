# Phase16 — Windows native installation acceptance

- Requirements: ASG-REL-001/002, ASG-TEST-004. Product candidate4378c6e; follow-up evidence commit does not change product code.
- Authorization: user explicitly requested full local installation/upgrade/crash/cleanup plan; no merge, publication, reboot or credentials changes.
- Safety: original six data files backed up with hashes, isolated, restored byte-identically after uninstall. Existing WebView cache predates tests and is excluded from cleanup. Test runtime checkpoint compressed and verified.
- Results: old40693d7 MSI→179876b→4378c6e direct upgrades succeed and replace ProductCodes; installed binaries match MSI-extracted payload. Actual packaged main process-tree interruption automatically compensates; external edits survive NEEDS_ATTENTION and retries do not replay. Work/delivery/layout/acceptance/recovery histories persist. English/Chinese8-page browser tour has no page/API failures; user confirmed native Environment no console flashes.
- Tool boundaries: native accessibility content read, but capture timed out and click geometry unavailable. Browser and human observations are distinguished. Earlier composite commands and deletion commands were rejected by automatic review; granular safe system operations succeeded, cleanup awaits explicit user response.
- Exit: runtime/upgrade and original restoration complete. Cleanup is not complete until actual permitted removals and measured report. No false release-ready, physical-device or all-native-input claim.
- Evidence: docs/verification/WINDOWS_INSTALL_FINAL_20261023.json; local raw reports retained in the installation test directory. No model calls in this phase.
