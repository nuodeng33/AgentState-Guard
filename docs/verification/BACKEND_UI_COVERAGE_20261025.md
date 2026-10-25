# Backend to UI coverage audit — 2026-10-25

Source snapshot: `8123a0e`. Read-only source inspection; no API requests, Core startup, model calls, WSL/container startup, installation, or native UI actions were performed. This audit establishes source wiring, not runtime availability. Preserve the simple work shell, dominant central work cards, and auxiliary Guard tools under Settings.

## Ordinary capabilities missing from the UI (P1)

Paths below are repository-relative. `W` means `/api/v1/works/{work_id}` and `S` means `/api/v1/discovery/sources/{source_id}`.

| Capability | Existing endpoint and implementation | Current UI evidence | Suggested location |
|---|---|---|---|
| Edit work title and owner | `PATCH W`; `agentguard/api/work.py:73`, `agentguard/work/service.py:277` | `web/src/pages/WorkPage.tsx:307` only submits create-work/task/artifact forms | Edit → Work information |
| Revise requirement with retained versions | `POST W/requirements`; `agentguard/api/work.py:77`, `agentguard/work/service.py:292` | WorkPage creates initial requirement and reads versions; no revision form | Edit → Revise requirement |
| Edit an existing task and task dependencies | `PUT W/tasks/{task_id}`; `agentguard/api/work.py:93`, `agentguard/work/service.py:312`; `depends_on` in `agentguard/work/models.py:40` | `WorkPage.tsx:308` only creates tasks, without dependency selection | Selected task card → Edit; Edit menu can act on selection |
| Rename session | `PATCH W/sessions/{session_id}`; `agentguard/api/execution.py:67`, `agentguard/execution/service.py:226` | `web/src/components/WorkSessions.tsx:549` offers connect/archive, no nickname editor | Session row actions |
| Link/unlink a protected directory, change batch, or observed session to work | `POST W/protection-links`, `POST W/protection-links/unlink`; `agentguard/api/protection.py:183`, `agentguard/recovery/protection.py:1086` | `web/src/components/WorkDelivery.tsx:123` only reads links to populate directory versions | Work → Related content / Protected directories |
| Source connect / explicit WSL start-and-connect / disconnect | `POST S/connect`, `POST S/start-and-connect`, `POST S/disconnect`; `agentguard/api/protection.py:193`, service `agentguard/recovery/protection.py:746`, domain guards `agentguard/recovery/protection_domains.py:413` | `web/src/components/ProtectionScopeManager.tsx:158` directs users to environment controls, but EnvironmentPage only observes/refreshes | Settings → Environment; explicit target and confirmation for starting stopped WSL |

The shared API client currently exposes GET/POST only (`web/src/api/client.ts:67`). PATCH/PUT editing needs the same authentication and error handling; POST must not impersonate those methods. Protection links accept **scope/batch/session**, not arbitrary versions (`agentguard/recovery/protection.py:1094`). Automatic execution bindings do not replace a manual link/unlink workflow.

## Implemented, but deeply hidden

| Capability and API | Current reachable UI | Coverage finding |
|---|---|---|
| Sessions, execution, cancel, responses: `W/sessions`, `W/runs/{run_id}/cancel`, `W/runs/{run_id}/responses` | Work title → More → Session management (`WorkPage.tsx:460`); `WorkSessions.tsx:396`, `:412`, `:419` | Add a visible View/left-work destination; preserve existing flow |
| One pending instruction: `W/sessions/{session_id}/pending-instruction` and `…/{pending_id}/cancel` | Sessions → Next instruction; `WorkSessions.tsx:571`, `WorkPendingInstructionPanel.tsx:159` | Implemented; unavailable/uncertain records must remain blocking |
| Delivery, review, acceptance: `W/submissions`, `…/{submission_id}/reviews`, `…/acceptances` | Work title → More → Delivery (`WorkPage.tsx:460`); `WorkDelivery.tsx:253`, `:291`, `:296` | File → Delivery and View → Delivery records are discoverability improvements |
| Restore preview/diff/apply/undo: `/api/v1/recovery/previews`, `/operations` | Work title → More → File recovery; `WorkRecovery.tsx:254`, `:266`, `:272` | Add File → File recovery; this is different from Guard configuration recovery |
| Register/configure directory protection | Recovery → Manage protected directories (`WorkRecovery.tsx:228`), session creation (`WorkSessions.tsx:517`); `ProtectionScopeManager.tsx:140` | Already supports enable/pause, retention and quota; do not report as absent |

## Missing optional file/version tools (P2)

| Capability | Endpoint / source | Current UI |
|---|---|---|
| Capture a directory version now | `POST /api/v1/protection/scopes/{scope_id}/capture`; `agentguard/api/protection.py:104` | No caller; automatic collection is implemented. Label separately from Save workspace layout |
| Pin/unpin a retained directory version | `POST …/versions/{version_id}/pin`; `agentguard/api/protection.py:120`, `agentguard/recovery/protection.py:523` | No pin control |
| Browse a file across versions | `GET …/files/{file_id}/history`; `agentguard/api/protection.py:128` | Run diffs and restore selection exist, but no full per-file history browser |

## Covered and intentionally auxiliary

- Work create/list/layout save and workbench/relationship/3D views: `WorkPage.tsx:438`; agents and colors: `WorkPage.tsx:535`. No 3D interaction changes are implied by this audit.
- Guard overview, environment, changes/evidence, supervision/controlled configuration changes, configuration checkpoint recovery, devices, language and AI settings are reachable via bottom-left profile → Settings → sections (`web/src/components/AppShell.tsx:29`, `:87`, `:113`; `SettingsPage.tsx:20`). AI analysis is in Environment/Supervision, not a general work generator.
- Source inspection/registration of programs and session stores exists in `DiscoveryCatalog.tsx:126` and `:140`, reached through Agents → Observation sources and session creation.
- `/api/health`, `/api/readiness`, `/api/session`, `/api/verify`, `/api/versions`, `/api/transactions`, `/api/handoff`, raw operation events/retained preimages, collection and legacy checkpoint import are diagnostics, lifecycle or advanced maintenance surfaces. Their presence is not a mandate for ordinary File/Edit/View commands.

## Safe-fail and non-capabilities

These are real implementations rather than demo fixtures, but operations remain subject to authoritative storage/domain/runtime checks. A missing runtime yields safe 503, not permission to fake success (`agentguard/api/execution.py:37`, `agentguard/api/protection.py:69`). Ordinary WSL connect must reject a stopped source; only explicit start-and-connect can start it. Stopped containers still report `DOMAIN_NOT_RUNNING`; native disconnect is unsupported (`agentguard/recovery/protection_domains.py:413`, `:454`). Observation enable/disable is not transport connect/disconnect.

No work-delete, work-archive, work-package import/export, or general Undo/Redo work API was found. Do not add decorative commands that claim those functions. Existing restore Undo is a specific validated recovery operation, not generic edit history.

## Follow-up status

This document records findings, not completion of the gaps. The first incremental UI closure should expose existing Sessions/Delivery/Recovery destinations and repair the missing Environment source controls. P1 editing and work protection associations remain separate implementation work. Runtime operations and actual user environments must not be exercised by this audit.
