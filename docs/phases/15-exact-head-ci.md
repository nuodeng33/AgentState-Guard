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
- Actual new-commit Core scheduling, browser acceptance, wheel and Windows lifecycle remain pending.
