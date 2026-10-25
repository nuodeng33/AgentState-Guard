# Phase27 — work shell capability coverage and central paper notes

## Scope

The work-first shell now keeps ordinary work in the center and exposes existing backend capabilities through File/Edit/View. Central requirement and task cards use restrained paper surfaces with realistic decorative pins; relationship task nodes remain compact pills. No API schema, database migration, authority, execution, or recovery semantics changed.

## Connected existing capabilities

- File: delivery, file recovery, protected-directory links, create/save/all work.
- Edit: work information PATCH, requirement revision POST, existing task PUT, create task/artifact.
- View: workbench, relationship space, 3D view, sessions, agents, agent colors.
- Settings > Environment: existing source connection controls with explicit WSL start confirmation and container/native boundaries.
- Session rows can rename an existing session using the existing PATCH endpoint; no execution or connection is triggered.

## Verification

Independent integrated frontend report: 388 passed / 388 total, 51 files, 0 failures/errors/skips; typecheck, production build, diff check passed. Backend CORS contract subset: 11 passed, 37 deselected; the fixed-origin allowlist now permits only existing GET/POST/PATCH/PUT verbs, while unknown origin and DELETE remain rejected and unauthenticated edits remain 401. Real isolated Core/browser proof reached work edit, requirement version history, task edit and protection-directory link/unlink; the final browser harness retained a lifecycle failure when closing duplicate stale directory-panel nodes before returning to relationship view. Central paper notes independently passed screenshots and 10 real checks, including drag, resize, focused reading, save/reload, compact relationship node geometry, narrow fit, and no page errors.

## Boundaries

Original business data remains uninstalled and six-file SHA-256 matched against immutable backup. No real model/provider turn, physical phone, or native pointer proof is claimed here. The final MSI/Windows CI must be rebuilt from the next pushed commit; the current production-asset browser proof is not installed-MSI proof.

## Rollback

Revert the phase27 source/components/assets together to `8123a0e8b6105f9c9cd1d13bd5a25bd8613754a1`; do not downgrade or rewrite database history.
