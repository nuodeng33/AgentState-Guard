# Phase 15 — Exact commit CI closure

- Requirements: ASG-TEST-004, ASG-REL-001/002. Base 40116551c981bf22d3cf46a01faccce56a65318c, branch fix/codex-empty-store.
- Authorization: user explicitly permits branch push and continued CI; no merge or publication. Current Codex login reuse does not authorize transferring credentials to CI.
- Entry: actual Core run39912640138 failed immediately with no jobs. Official actionlint1.7.7 reproduces invalid runner context at core.yml43, job-level env.
- Scope: move unchanged PLAYWRIGHT_MODULE to actual acceptance step env, which supports runner context and is inherited by the browser subprocess. Installer still uses RUNNER_TEMP directly.
- Non-goals: no product/schema/protocol/dependency/permission changes, no sandbox bypass, no model or secret transfer.
- Risk: consumer must receive the exact installed Playwright module path; step inheritance reviewed independently.
- Verification: actionlint red→green, diff check, independent full Python regression; push exact resulting commit and audit actual Core and Windows jobs, artifacts and reports. No counts summed across runs.
- Exit: actual newest commit Core and Windows evidence audited; any remaining failure or environment gap explicitly recorded. Local lint alone does not satisfy this exit.
- Rollback: revert isolated phase commit, preserving prior features and local runtime evidence.

## Local verification

- Independent actionlint and diff check exit0. Full Python regression: 1375 passed, 26 skipped, 0 failures/errors, 88.98s, existing684 deprecation warnings. Project JUnit parser confirms1401 cases in artifacts/ci-context/full.xml; full.log retained. No model calls or HOME override.
- Initial Windows run39912641361 logs show native recovery-process job35 passed; frontend and sidecar jobs succeeded, Tauri build still running at this checkpoint. Artifact ZIP retrieval via connected download reference returned HTTP403 in this environment; do not claim locally parsed Windows XML yet. No secret/proxy/network-policy modification.
- Historical checkpoint superseded by the final remote audit below.

## Final remote audit — 2026-10-23

- Product candidate: 40693d78d5e5f7bd9ff046bee72da1e5c1ddea3f. Core run39913027492 and Windows run39913027509 both terminal SUCCESS; every job succeeded.
- Downloaded actual Python3.11/3.12/3.13 JUnit XML, parsed separately using scripts/parse-test-results.py: each1375 passed,26 skipped,0 failed/errors;1401 cases. No cross-run summing.
- Downloaded real Linux UI report: run_id b39dc9ad-4471-43e4-8987-ea782c7bb004,22 required checks strictly true. Frontend job log:244 passed. Wheel log verifies install outside checkout, exact product SHA and pip check.
- Windows final logs confirm PACKAGED_MAIN_CORE_SMOKE_VERIFIED, DESKTOP_HEALTH_VERIFIED, DESKTOP_SMOKE_VERIFIED, process-tree cleanup and Uninstall OK. This closes the new-install CI lifecycle, not upgrade or full interactive acceptance.
- Remote artifact ZIP digests and downloaded EXE PE/MSI compound-file headers checked locally; per-file hashes recorded in ../verification/CI_40693d7_20261023.json. Earlier cloud download403 is historical, not the current audit result.
- Fix-branch push filters do not trigger Android/CodeQL. No five-workflow or all-platform claim. Real provider, upgrade, real DACL, packaged crash/business recovery and physical-device gates remain open.
- Follow-up commit is evidence-only; it does not change the product candidate or claim new product testing. No model calls, credentials transfer to CI, merge or publication.

- Downloaded Windows recovery-process XML parsed by project parser:35 passed,0 failed/errors/skipped. Counts are a separate Windows job, not added to full-suite totals.
