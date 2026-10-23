# Phase18 — Spatial interaction closure

Requirement ASG-CLOSE-001, base1f66480. User explicitly requested all gaps in the prior capability table, including spatial interaction/color/viewer, then queue and environment/device/AI integration. Phase17 enrollment is complete; it does not finish the broader request.

Scope: real child groups for actual Agent/task associations; continuous hover/focus, explicit pin/unpin, Escape precedence and parentward return; deterministic user color selection; existing 3D viewer lifecycle/readability/keyboard corrections. No fabricated agent progress or links. Core stays larger than tool bubbles; demo remains interaction reference only.

Pending architecture decision: user asked via async input whether minimum compatible API/DB changes are permitted because the prior installation plan default froze APIs/schema. Do not implement dependent persistence or queue migration before reply. UI/viewer and existing-contract device/AI fixes can continue.

Affected: WorkBubbleGroup, WorkPage, WorkAgents, WorkVisuals, WorkRestScene, scoped CSS, pure appearance helper, tests. Optional existing view extension only after permission. No credential or provider authority changes.

Risks: hover/focus races, reversed exit, stale work callbacks, view receipt digests, renderer reset. Rollback1f66480, no schema change in initial spatial UI work.

Tests: interaction component tests; actual browser position sampling during collapse, pointer continuity/pinning/focus, desktop/mobile/reducedmotion; original work regressions; 3D real WebGL/fallback/camera controls; persistence rejection/reopen after any approved API extension. Final fullfrontend/backend where modified; exact-product CI after final implementation.

Tasks: design[x]; implementation[in progress]; browser/regression[ ]; persistence decision[pending]; final evidence/push[ ].
