# Phase26 — work-first desktop shell

Requirement WS-007/008. User2026-10-25 explicitly requested: My work as the default; Codex-like left work list and bottom avatar → Settings; top File/Edit/View. This supersedes the initial top-bar tools proposal. No additional approval is needed for this reversible UI scope.

## Implementation and boundaries

Default `/` enters Work; explicit work/workspace queries remain. Left sidebar owns New work, local loaded-work search, selection and pagination. Center shows the selected work or quiet starting state, with no duplicate grid or detection dashboard. Local avatar opens only Settings; all Guard/environment/changes/supervision/recovery/device/preferences surfaces remain there. File/Edit/View call existing new/save/all-work, task/artifact and workbench/space/3D handlers; unavailable actions are disabled. Work remains mounted across settings visits. Direct switching saves the previous session through its existing exact receipt/CAS flow; confirmed creation refreshes the list. Capture-phase Escape closes popovers before workspace navigation. No API/schema/permission/3D behavior changes.

## Evidence

Independent fullfrontend337passed/47files,0failed/errors/skips; typecheck/build passed. Actual browser caught a CSS collision between new menu groups and old `.work-menu`; a unique class fixed it, focused6tests/build passed afterward. Original failed logs retained.

Windows sandboxedChrome ran rebuilt production assets against unchanged actual installed17fCore normal APIs/test data. File→Create work201, Edit→Add task, View→Workbench, Save, avatar→Settings→Environment→My work and exact-layout preservation passed;1024px no document overflow, no page errors. Source preview is not newMSI/native pointer acceptance. Search/list-return final browser check was interrupted by test-Core shutdown and is not claimed. See verification/WORK_FIRST_BROWSER_20261025.json and WORK_FIRST_FRONTEND_20261025.json.

## Native state and remaining gates

17f all4CIgreen, direct0c88→17fMSI/actualpayload/9GUI-Core checks passed; formal newAPK MuMu1 pairing/reconnect/restart/revoke/unpair/Disable12checks passed. Exact ASG firewall rules absent and8788TCP/UDP free. Final packaged compensation16checks passed; external-editcase remains FAIL(PROCESS_PATH_UNAVAILABLE on transient startup process identity). Original0c88 two-case success remains separate. No guard bypass or unknown exe termination. Native capture still timed out.

TestMSI uninstalledexit0, ProductCode/programdirectory absent. Mutated testdata archived, six originalfiles3,199,253bytes restoredexactSHA256 against immutablebackup; originalDBnotopened. No permanent deletion/reboot/merge/release. Overall goal remains active; next exactCI/newUIpackage and unresolved native/provider/physical scopes stay explicit.

## Rollback / exit

Revert phase26 shell/components/copy/tests/generatedassets together to17f8751; no DBdowngrade/historyrewrite. Preserve restoredoriginaldata and allfailed evidence. Exit: fullfrontend +actualbrowser scope done; exactnewCI/package acceptance still required. Routine choice is familiar restrained layout using existingtokens/fonts; no invented account identity or placeholder command.
