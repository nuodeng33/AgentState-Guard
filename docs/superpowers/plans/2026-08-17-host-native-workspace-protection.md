# Host-native Workspace Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让唯一 verified Windows Host-native Agent workspace 自动成为 Core-owned protection scope，并完成 coverage、checkpoint、diff/evidence、Test Restore、quarantine Restore 与 post-restore verification。

**Architecture:** 保留 `ProductDiscoveryService`、Snapshot V3、`RecoveryService`、Evidence Ledger 和现有 `/api/v1/recovery` authority。新增不可序列化的 exact CWD observation、durable workspace scope observation、Snapshot V3 workspace extension、deterministic workspace adapter 和 safe projections；caller 仍不能提交路径/domain/manifest。

**Tech Stack:** Python 3.11+, FastAPI, SQLite, psutil, pytest, stdlib `pathlib`/`hashlib`/`ctypes`, existing Snapshot V3 and Evidence Ledger.

## Global Constraints

- Host-native Windows 是独立 V1 path；Docker、WSL、container capability absence 不得阻塞。
- 不修改 K3 React/Compose presentation。
- 不跟随 symlink/junction/reparse point，不跨 workspace root，不做 whole-host 或 arbitrary-path restore。
- Workspace change 固定 `UNATTRIBUTED`，除非未来存在独立因果 evidence；本计划不新增该 evidence。
- Created/restorable residue 优先移动到 ASG-owned quarantine；绝不 hard-delete。
- Quarantine 失败必须返回 `RESTORABLE_SET_RESTORED` 和 `POST_CHECKPOINT_RESIDUE_PRESENT`，不能声称完整 Workspace Restore verified。
- Workspace permission/DACL capture/restore/verify 全部使用当前用户权限；不触发 UAC。
- UAC helper 只属于 Device Link firewall plan。
- 每个 production change 前先运行对应 RED test；一次只关闭一个 root cause。

---

## File map

- Create `agentguard/discovery/workspace_authority.py`: exact CWD authority records、safe root resolution、Windows reparse/broad-root rejection。
- Modify `agentguard/discovery/agents/models.py`: internal `ProcessWorkspaceAuthority` immutable type。
- Modify `agentguard/discovery/agents/processes.py`: 在同一 process observation 中返回 sanitized candidate 与 exact authority record。
- Modify `agentguard/discovery/product.py`: `ProductDiscoveryReport` 与 `discover_with_authority()`；`discover()` 保持兼容。
- Create `agentguard/recovery/workspace_permissions.py`: current-user permission proof protocol、POSIX/Windows implementations。
- Create `agentguard/recovery/workspace_policy.py`: 四类 coverage、bounded scan、safe summaries。
- Create `agentguard/recovery/workspace_adapter.py`: Snapshot V3 workspace artifact、Test Restore、staged Restore、quarantine、verification。
- Create `agentguard/recovery/workspace_scope.py`: durable scope binding、checkpoint lookup、diff 与 Ledger append。
- Modify `agentguard/recovery/manifest.py`: backward-compatible workspace extension validation/digest。
- Modify `agentguard/recovery/contracts.py`: safe result details only；不增加 caller authority input。
- Modify `agentguard/recovery/service.py`: 保留 orchestration，接受 multi-file workspace adapter result和 degraded post-restore result。
- Modify `agentguard/recovery/coverage.py`: workspace coverage projection与 existing R0-R3 facts并存。
- Modify `agentguard/storage/migrations.py`: migration 9 scope observations与 quarantine records。
- Modify `agentguard/api/product_recovery.py`: server-owned workspace/config context resolution。
- Modify `agentguard/api/server.py`: startup/refresh scope bind、observation、affected views。
- Modify `agentguard/api/r4_projection.py`: allowlisted coverage/change/attribution/post-restore fields。
- Tests: new focused files plus current discovery/recovery/API/migration regression files。

---

### Task 1: Exact Host-native workspace authority

**Files:**
- Create: `agentguard/discovery/workspace_authority.py`
- Modify: `agentguard/discovery/agents/models.py`
- Modify: `agentguard/discovery/agents/processes.py`
- Modify: `agentguard/discovery/product.py`
- Modify: `agentguard/discovery/agents/__init__.py`
- Test: `tests/test_host_workspace_authority.py`
- Test: `tests/test_product_discovery.py`

**Interfaces:**
- Produces: `ProcessWorkspaceAuthority`, `ProductDiscoveryReport`, `ResolvedWorkspaceAuthority`, `resolve_host_workspace(report, *, home_path, system_roots)`。
- Preserves: `ProductDiscoveryService.discover() -> DiscoverySnapshot`。

- [ ] **Step 1: Write RED authority tests**

```python
def test_same_root_codex_and_claude_resolve_one_host_scope(tmp_path):
    report = _report(tmp_path, agents=("codex.exe", "claude.exe"))
    resolved = resolve_host_workspace(report, home_path=tmp_path.parent)
    assert resolved.status == "BOUND"
    assert resolved.root_path == tmp_path.resolve()
    assert resolved.agent_ids == tuple(sorted(resolved.agent_ids))

def test_distinct_agent_roots_fail_closed(tmp_path):
    resolved = resolve_host_workspace(_two_root_report(tmp_path), home_path=tmp_path.parent)
    assert resolved.status == "UNAVAILABLE"
    assert resolved.reason_code == "WORKSPACE_SCOPE_AMBIGUOUS"

def test_docker_and_wsl_absence_do_not_block_windows_scope(tmp_path):
    report = _windows_report(tmp_path, docker=False, wsl=False)
    assert resolve_host_workspace(report, home_path=tmp_path.parent).status == "BOUND"
```

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/test_host_workspace_authority.py tests/test_product_discovery.py`

Expected: collection/import failure for the new authority types and resolver.

- [ ] **Step 3: Add exact authority types and collection**

```python
@dataclass(frozen=True)
class ProcessWorkspaceAuthority:
    process_instance_id: str
    candidate_id: str
    execution_domain_id: str
    cwd: Path
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class ProductDiscoveryReport:
    snapshot: DiscoverySnapshot
    workspace_authorities: tuple[ProcessWorkspaceAuthority, ...]
```

`ProcessCollectionResult.to_dict()` must omit `workspace_authorities`. `discover_with_authority()` constructs one report from the same process enumeration; `discover()` returns `report.snapshot`。

- [ ] **Step 4: Implement bounded root resolution**

`resolve_host_workspace()` must normalize Windows case, merge nested CWDs under the same nearest `.git` root, reject root/home/system/temp/AppData and any reparse path component, and return exactly one of `BOUND`, `NOT_OBSERVED`, `UNAVAILABLE` with stable reason code.

- [ ] **Step 5: Run GREEN and regression tests**

Run: `python -m pytest -q tests/test_host_workspace_authority.py tests/test_product_discovery.py tests/test_discovery_processes.py tests/test_discovery_workspaces.py tests/test_discovery_psutil_backend.py`

Expected: all pass; serialized discovery JSON contains no exact user-home path.

- [ ] **Step 6: Commit**

```text
git add agentguard/discovery tests/test_host_workspace_authority.py tests/test_product_discovery.py
git commit -m "feat(discovery): 建立 Host-native workspace authority"
```

---

### Task 2: Durable scope binding and migration

**Files:**
- Create: `agentguard/recovery/workspace_scope.py`
- Modify: `agentguard/storage/migrations.py`
- Modify: `agentguard/evidence/models.py`
- Modify: `agentguard/api/server.py`
- Test: `tests/test_workspace_scope_binding.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `ResolvedWorkspaceAuthority`。
- Produces: `WorkspaceScopeService.bind(report, recorded_at) -> WorkspaceScopeResult`, `active_scope() -> DurableWorkspaceScope | None`。

- [ ] **Step 1: Write RED migration/binding tests**

```python
def test_scope_row_and_ledger_event_commit_together(database, resolved_scope):
    result = WorkspaceScopeService(database).bind(resolved_scope, recorded_at=NOW)
    assert result.reason_code == "WORKSPACE_PROTECTION_BOUND"
    assert verify_ledger(database._conn) == []
    assert database._conn.execute(
        "SELECT root_path FROM workspace_scope_observations WHERE observation_id = ?",
        (result.observation_id,),
    ).fetchone()[0] == str(resolved_scope.root_path)

def test_scope_ledger_failure_rolls_back_private_path_row(database, monkeypatch):
    monkeypatch.setattr(workspace_scope, "verify_ledger", lambda _conn: ["bad"])
    with pytest.raises(WorkspaceScopeError):
        WorkspaceScopeService(database).bind(_scope(), recorded_at=NOW)
    assert _scope_count(database) == 0
```

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/test_workspace_scope_binding.py tests/test_migrations.py`

Expected: missing migration/table/service failure.

- [ ] **Step 3: Add migration 9**

Create append-oriented `workspace_scope_observations` and `workspace_quarantine_records`. Scope rows allow `root_path` only in local DB; corresponding Ledger payload contains `root_digest`, opaque IDs, status/reason and evidence refs only.

- [ ] **Step 4: Implement atomic scope binding**

```python
class WorkspaceScopeService:
    def bind(self, resolved: ResolvedWorkspaceAuthority, *, recorded_at: datetime) -> WorkspaceScopeResult: ...
    def active_scope(self) -> DurableWorkspaceScope | None: ...
```

`bind()` uses `StateDB.transaction()` for row plus `WORKSPACE_PROTECTION_BOUND`; invalid Ledger, invalid root digest or conflicting latest observation fails closed.

- [ ] **Step 5: Wire startup/refresh without caller fields**

`_refresh_product_discovery()` calls `discover_with_authority()` only when implemented by the real service, records discovery, then binds the scope. Fake/legacy discovery reports `WORKSPACE_SCOPE_NOT_OBSERVED`; it must never derive a path from DTO fields.

- [ ] **Step 6: Run GREEN tests**

Run: `python -m pytest -q tests/test_workspace_scope_binding.py tests/test_migrations.py tests/test_product_discovery.py tests/test_authoritative_workspace_binding.py`

- [ ] **Step 7: Commit**

```text
git add agentguard/storage/migrations.py agentguard/evidence/models.py agentguard/recovery/workspace_scope.py agentguard/api/server.py tests
git commit -m "feat(authority): 持久化 server-owned workspace scope"
```

---

### Task 3: Four-category coverage and Snapshot V3 extension

**Files:**
- Create: `agentguard/recovery/workspace_permissions.py`
- Create: `agentguard/recovery/workspace_policy.py`
- Modify: `agentguard/recovery/manifest.py`
- Test: `tests/test_workspace_policy.py`
- Test: `tests/test_workspace_permissions.py`
- Test: `tests/test_recovery_manifest.py`

**Interfaces:**
- Produces: `PermissionProof`, `PermissionBackend`, `WorkspaceCoverageEntry`, `WorkspaceScan`, `scan_workspace(root, *, permission_backend, limits)`, `validate_workspace_extension()`。

- [ ] **Step 1: Write RED coverage and permission tests**

```python
def test_scan_reports_all_four_categories(tmp_path, permission_backend):
    scan = _scan_fixture_with_restorable_audit_excluded_unreachable(
        tmp_path, permission_backend
    )
    assert scan.counts == {
        "restorable": 1,
        "audit_only": 1,
        "excluded": 1,
        "unreachable": 1,
    }

def test_permission_capability_absence_never_requests_elevation(tmp_path):
    backend = _UnavailablePermissionBackend()
    entry = scan_workspace(tmp_path, permission_backend=backend).entries[0]
    assert entry.category == "unreachable"
    assert entry.reason_code == "WORKSPACE_PERMISSION_CAPTURE_UNAVAILABLE"
    assert backend.elevation_calls == 0
```

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/test_workspace_policy.py tests/test_workspace_permissions.py tests/test_recovery_manifest.py`

- [ ] **Step 3: Implement current-user permission backends**

```python
class PermissionBackend(Protocol):
    def capture(self, path: Path) -> PermissionProof: ...
    def apply(self, path: Path, proof: PermissionProof) -> None: ...
    def verify(self, path: Path, proof: PermissionProof) -> bool: ...
```

POSIX captures mode. Windows captures only current-user-readable file attributes and owner/DACL SDDL subset through stdlib `ctypes`; it never calls `runas`. Any API denial maps to stable capture/apply/verify reason codes.

- [ ] **Step 4: Implement deterministic bounded scan**

Classify ordinary bounded files as `restorable`; sensitive/oversize as `audit_only`; fixed dependency/cache/generated/reparse/special objects as `excluded`; permission/race failures as `unreachable`. Never follow reparse points. Return safe count/reason summaries without absolute paths.

- [ ] **Step 5: Extend Snapshot V3 validation compatibly**

`manifest_digest(snapshot)` hashes the legacy manifest exactly when no workspace extension exists. With an extension, hash canonical `{manifest, workspace}`. Strictly validate extension field set, root digest, relative containment, coverage uniqueness, counts, permission proof digest and manifest-to-coverage correspondence.

- [ ] **Step 6: Run GREEN tests and legacy manifest regression**

Run: `python -m pytest -q tests/test_workspace_policy.py tests/test_workspace_permissions.py tests/test_recovery_manifest.py tests/test_recovery_service.py tests/test_recovery_coverage.py`

- [ ] **Step 7: Commit**

```text
git add agentguard/recovery/workspace_permissions.py agentguard/recovery/workspace_policy.py agentguard/recovery/manifest.py tests
git commit -m "feat(recovery): 增加 workspace coverage 与权限证明"
```

---

### Task 4: Workspace checkpoint and safe Recovery projection

**Files:**
- Create: `agentguard/recovery/workspace_adapter.py`
- Modify: `agentguard/api/product_recovery.py`
- Modify: `agentguard/recovery/service.py`
- Modify: `agentguard/recovery/coverage.py`
- Modify: `agentguard/api/r4_projection.py`
- Modify: `agentguard/api/server.py`
- Test: `tests/test_workspace_checkpoint.py`
- Test: `tests/test_product_recovery_api.py`

**Interfaces:**
- Consumes: active `DurableWorkspaceScope`, `WorkspaceScan`, permission backend。
- Produces: `HostWorkspaceRecoveryAdapter`, recovery fields `scope_kind`, `workspace_id`, `coverage`。

- [ ] **Step 1: Write RED workspace checkpoint API test**

```python
def test_empty_checkpoint_request_uses_active_host_workspace(client, workspace):
    response = client.post("/api/v1/recovery/checkpoints", json={}, headers=_auth(client))
    assert response.status_code == 200
    body = response.json()
    assert body["scope_kind"] == "HOST_WORKSPACE"
    assert body["workspace_id"]
    assert body["coverage"]["restorable"] >= 1
    assert "root_path" not in json.dumps(body)
```

- [ ] **Step 2: Run RED test**

Run: `python -m pytest -q tests/test_workspace_checkpoint.py tests/test_product_recovery_api.py`

- [ ] **Step 3: Implement workspace snapshot adapter**

`HostWorkspaceRecoveryAdapter.snapshot()` validates the root binding, scans once, emits manifest entries and blobs for restorable files, audit-only metadata without blobs, and the validated workspace extension. It returns exact coverage in safe `details`.

- [ ] **Step 4: Resolve recovery context server-side**

Snapshot uses latest active verified scope. Test/Restore loads checkpoint metadata, validates workspace extension, then resolves the exact durable scope observation; mismatch returns `WORKSPACE_SCOPE_BINDING_INVALID`. If no Agent workspace was observed, existing product-config behavior remains and reports `scope_kind=PRODUCT_CONFIG`.

- [ ] **Step 5: Project coverage without paths**

Add allowlisted aggregate counts/reasons and `scope_kind` to action/recovery DTOs. Keep current R0-R3 fields. Do not expose extension root, raw manifest, DACL text or sensitive relative names.

- [ ] **Step 6: Run GREEN and injection tests**

Run: `python -m pytest -q tests/test_workspace_checkpoint.py tests/test_product_recovery_api.py tests/test_api_r4_contract.py tests/test_product_projections.py`

- [ ] **Step 7: Commit**

```text
git add agentguard/recovery/workspace_adapter.py agentguard/api agentguard/recovery/service.py agentguard/recovery/coverage.py tests
git commit -m "feat(recovery): 连接 Host-native workspace checkpoint"
```

---

### Task 5: Workspace diff and unattributed Evidence

**Files:**
- Modify: `agentguard/recovery/workspace_scope.py`
- Modify: `agentguard/api/server.py`
- Modify: `agentguard/api/r4_projection.py`
- Test: `tests/test_workspace_changes_evidence.py`
- Test: `tests/test_product_projections.py`

**Interfaces:**
- Produces: `WorkspaceScopeService.observe_latest_checkpoint() -> WorkspaceObservationResult` and allowlisted change DTO fields。

- [ ] **Step 1: Write RED create/modify/delete attribution test**

```python
def test_refresh_records_workspace_diff_without_agent_attribution(workspace_app):
    checkpoint = workspace_app.checkpoint()
    _modify_delete_create(workspace_app.workspace)
    workspace_app.refresh()
    changes = workspace_app.changes()
    assert {item["change_kind"] for item in changes["items"]} == {
        "CREATED", "MODIFIED", "DELETED"
    }
    assert {item["attribution"] for item in changes["items"]} == {"UNATTRIBUTED"}
    assert all(item.get("agent_id") is None for item in changes["items"])
    assert checkpoint in {item["checkpoint_id"] for item in changes["items"]}
```

- [ ] **Step 2: Run RED test**

Run: `python -m pytest -q tests/test_workspace_changes_evidence.py tests/test_product_projections.py`

- [ ] **Step 3: Implement bounded diff and deduplicated Ledger events**

Compare baseline coverage with a fresh scan. Use deterministic event/state digest over scope/checkpoint/target/change/current digest, query existing event ID before append, and write `OBSERVED_CHANGE` with `source=workspace-observer`, subject workspace ID and `attribution=UNATTRIBUTED`.

- [ ] **Step 4: Extend Changes/Evidence allowlists**

Project only `change_kind`, `coverage_category`, `recovery_disposition`, `attribution`, safe workspace-relative label, checkpoint/scope IDs and digests. Created entries use `NOT_IN_CHECKPOINT`, never `RECOVERABLE`.

- [ ] **Step 5: Run GREEN and privacy regression**

Run: `python -m pytest -q tests/test_workspace_changes_evidence.py tests/test_product_projections.py tests/test_authoritative_workspace_binding.py tests/test_evidence_ledger.py`

- [ ] **Step 6: Commit**

```text
git add agentguard/recovery/workspace_scope.py agentguard/api tests/test_workspace_changes_evidence.py tests/test_product_projections.py
git commit -m "feat(evidence): 记录 unattributed workspace diff"
```

---

### Task 6: Test Restore, quarantine Restore, and post-verification

**Files:**
- Modify: `agentguard/recovery/workspace_adapter.py`
- Modify: `agentguard/recovery/service.py`
- Modify: `agentguard/api/product_recovery.py`
- Modify: `agentguard/api/r4_projection.py`
- Test: `tests/test_workspace_test_restore.py`
- Test: `tests/test_workspace_restore.py`
- Test: `tests/test_product_recovery_api.py`

**Interfaces:**
- Produces: workspace adapter `test_restore()`/`restore()` with `post_restore_status`, `quarantined_targets`, `residue_targets`, `verified_targets`。

- [ ] **Step 1: Write RED Test Restore and quarantine tests**

```python
def test_test_restore_verifies_all_restorable_without_live_mutation(workspace_service):
    checkpoint = workspace_service.checkpoint()
    before = workspace_service.live_digest()
    result = workspace_service.test_restore(checkpoint)
    assert result.reason_code == "TEST_RESTORE_VERIFIED"
    assert result.details["verified_targets"] == 2
    assert workspace_service.live_digest() == before

def test_restore_quarantines_created_restorable_file(workspace_service):
    checkpoint = workspace_service.checkpoint()
    created = workspace_service.workspace / "created.txt"
    created.write_text("preserve me", encoding="utf-8")
    result = workspace_service.restore(checkpoint)
    assert result.reason_code == "WORKSPACE_RESTORED_AND_VERIFIED"
    assert not created.exists()
    assert workspace_service.quarantined_content(result) == b"preserve me"

def test_quarantine_failure_never_deletes_and_degrades_result(workspace_service):
    checkpoint = workspace_service.checkpoint()
    created = workspace_service.created_restorable()
    workspace_service.fail_quarantine_move()
    result = workspace_service.restore(checkpoint)
    assert created.exists()
    assert result.reason_code == "RESTORABLE_SET_RESTORED"
    assert result.details["post_restore_status"] == "POST_CHECKPOINT_RESIDUE_PRESENT"
```

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -q tests/test_workspace_test_restore.py tests/test_workspace_restore.py tests/test_product_recovery_api.py`

- [ ] **Step 3: Implement isolated multi-file Test Restore**

Materialize only restorable entries under a service-owned temporary root using safe relative paths. Apply and verify current-user permission proof, hash, size and manifest correspondence; cleanup is service-owned and live workspace remains untouched.

- [ ] **Step 4: Implement staged restore and ASG-owned quarantine**

Preflight target containment/permissions and quarantine destination. Stage baseline bytes, move Created/restorable files with `os.replace` into product-state quarantine on the same volume, restore modified/deleted baseline files, apply proof, then verify. Never move audit/excluded/unreachable entries. If atomic quarantine is unavailable, leave residue and degrade rather than copy/delete.

- [ ] **Step 5: Implement rollback and truthful outcomes**

On mutation failure, restore staged originals and move quarantined items back where safe. Rollback uncertainty returns `WORKSPACE_RESTORE_EXTERNAL_EFFECT_UNKNOWN`. Complete verified requires zero residue, all hashes/sizes/permission proofs matched, valid manifest and successful Ledger writes.

- [ ] **Step 6: Run GREEN, fault, and security regressions**

Run: `python -m pytest -q tests/test_workspace_test_restore.py tests/test_workspace_restore.py tests/test_product_recovery_api.py tests/test_recovery_fault_injection.py tests/test_recovery_service.py tests/test_recovery_test_restore.py`

- [ ] **Step 7: Commit**

```text
git add agentguard/recovery agentguard/api tests/test_workspace_test_restore.py tests/test_workspace_restore.py tests/test_product_recovery_api.py
git commit -m "feat(recovery): 完成 workspace quarantine restore"
```

---

### Task 7: Host-native end-to-end contract and documentation

**Files:**
- Test: `tests/test_host_native_workspace_e2e.py`
- Modify: `docs/FRONTEND_BACKEND_CONTRACT.md`
- Modify: `docs/CORE_BACKEND_CAPABILITY_MAP.md`
- Modify: `docs/spec/ACCEPTANCE_MATRIX.md`
- Modify: `docs/CURRENT_STATE.md`
- Modify: `docs/SESSION_HANDOFF.md`
- Modify: `docs/ROLLBACK_LEDGER.md`
- Modify: `docs/IMPLEMENTATION_LOG.md`

**Interfaces:**
- Verifies the complete public/server-owned flow and documentation truthfulness。

- [ ] **Step 1: Add full host-native contract test**

The test starts a Windows-domain fake psutil backend with native `codex.exe`, Docker/WSL capabilities absent, one real temp workspace, then performs discovery → checkpoint → modify/delete/create → refresh → Changes/Evidence → Test Restore → confirmed Restore → post-verification. It asserts no request accepts path/domain and every change is `UNATTRIBUTED`.

- [ ] **Step 2: Run end-to-end targeted suite**

Run: `python -m pytest -q tests/test_host_native_workspace_e2e.py tests/test_host_workspace_authority.py tests/test_workspace_scope_binding.py tests/test_workspace_policy.py tests/test_workspace_checkpoint.py tests/test_workspace_changes_evidence.py tests/test_workspace_test_restore.py tests/test_workspace_restore.py`

- [ ] **Step 3: Update frozen contract additively**

Document new response fields and reason codes, quarantine semantics, permission proof boundary, `UNATTRIBUTED` rule and K3 consumption needs. Do not prescribe new visuals or alter Device Link remote authority.

- [ ] **Step 4: Run complete verification**

Run: `python -m pytest -q`

Run: `python -m ruff check agentguard tests`

Run: `python -m compileall -q agentguard tests`

Expected: record exact pass/fail counts; platform-specific failures remain separately classified and no PASS is claimed from source review alone.

- [ ] **Step 5: Commit docs/evidence**

```text
git add tests/test_host_native_workspace_e2e.py docs
git commit -m "docs: 记录 Host-native workspace closure 证据"
```
