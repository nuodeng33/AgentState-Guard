# Windows Device Link Firewall Elevation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让普通 MSI 安装用户在启用 Device Link 时通过产品自身最小、scoped、可审计的 UAC helper 安装/移除固定 8788 firewall rules，无需手工 PowerShell/netsh，同时保持 network profile 与 workspace recovery authority 不变。

**Architecture:** ordinary Core sidecar 先完成既有 private physical LAN/default-route validation，再调用 `ShellExecuteExW(runas)` 启动同一已签名 Tauri executable 的严格 helper mode。helper 重做安全校验，只接受固定 apply/remove verb 和 validated RFC1918 address/prefix，执行固定 Private-profile TCP/UDP 8788 rules 后退出。Core 仅在 helper success 后启动 listener；任何缺失、拒绝或失败均 fail-closed。

**Tech Stack:** Python 3.11+ stdlib `ctypes`, Rust/Tauri, Windows `ShellExecuteExW`, PowerShell read-only network facts, `netsh advfirewall`, pytest, Cargo tests.

## Global Constraints

- UAC helper 只服务 Device Link scoped firewall；普通 workspace discovery/checkpoint/recovery 永不调用它。
- 不自动调用 `Set-NetConnectionProfile`，不把 Public profile 改为 Private。
- 不接受 arbitrary executable、command、port、profile、rule name、remote scope 或 shell text。
- helper 必须重验 physical default-route、Private profile、RFC1918 IPv4 与 prefix；不能只信 ordinary process 输入。
- listener 只能在 firewall apply 成功后启动。
- apply/remove 事件写入可审计但不含敏感网络细节的 Evidence；UAC cancel/failed/unavailable 使用稳定 reason code。
- 不新增 generic privileged helper，不修改 K3 UI。
- 每个 production change 前先运行对应 RED test。

---

## File map

- Create `agentguard/device_link/windows_elevation.py`: fixed-operation elevation runner and stable failure mapping.
- Modify `agentguard/device_link/firewall.py`: injected elevation path on production Windows; keep deterministic fake backend for tests/non-Windows.
- Modify `agentguard/device_link/controller.py`: listener-after-firewall ordering and truthful disable degradation.
- Modify `agentguard/device_link/contracts.py`: stable firewall result/reason codes if central definitions exist here.
- Modify `agentguard/api/server.py`: receive Tauri helper path from trusted process environment only.
- Create `desktop/src-tauri/src/firewall_helper.rs`: strict early CLI helper and independent validation.
- Modify `desktop/src-tauri/src/main.rs`: dispatch helper mode before constructing the Tauri app.
- Modify `desktop/src-tauri/src/sidecar.rs`: pass the canonical current executable path to Core as a dedicated environment variable.
- Modify `desktop/src-tauri/Cargo.toml`: add only minimal Windows API features if required.
- Test Python Device Link firewall/controller suites.
- Test Rust helper argument and command construction modules.
- Modify `docs/FRONTEND_BACKEND_CONTRACT.md` and `docs/device-link/DESKTOP_SECURITY.md`.

---

### Task 1: Define strict elevation invocation and error semantics

**Files:**
- Create: `agentguard/device_link/windows_elevation.py`
- Modify: `agentguard/device_link/firewall.py`
- Test: `tests/test_windows_firewall_elevation.py`

**Interfaces:**
- `WindowsFirewallElevationRunner.apply(address, prefix_length) -> ElevationResult`
- `WindowsFirewallElevationRunner.remove(address, prefix_length) -> ElevationResult`
- Trusted helper path comes only from the sidecar environment and must resolve to an existing regular `.exe`.

- [ ] **Step 1: Write RED validation and mapping tests**

```python
def test_elevation_refuses_missing_helper_path(monkeypatch):
    result = _runner(monkeypatch, helper=None).apply("192.168.1.10", 24)
    assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE"

def test_uac_cancel_is_fail_closed(fake_shell_execute):
    fake_shell_execute.cancel()
    result = _runner(fake_shell_execute).apply("192.168.1.10", 24)
    assert not result.applied
    assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_DECLINED"

def test_elevation_rejects_non_private_address():
    result = _runner().apply("203.0.113.4", 24)
    assert result.reason_code == "DEVICE_FIREWALL_SCOPE_INVALID"
```

- [ ] **Step 2: Run focused tests and record RED**

Run: `pytest -q tests/test_windows_firewall_elevation.py`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement minimum `ShellExecuteExW` wrapper**

Implementation requirements:
- Validate canonical helper path, verb, IPv4, and prefix before invoking Windows.
- Build arguments from a fixed token list; no shell, PowerShell command, or caller string concatenation.
- Use `runas`, wait for the process, read only its exit code, close handles.
- Map `ERROR_CANCELLED`/exit codes to stable reason codes.
- Never log the full command line or expose local path in projection.
- Provide an injected launcher protocol for deterministic tests.

- [ ] **Step 4: Run tests**

Run: `pytest -q tests/test_windows_firewall_elevation.py tests/test_device_link_firewall.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
feat(device-link): 增加受限 Windows firewall elevation runner
```

---

### Task 2: Build the fixed Tauri firewall helper mode

**Files:**
- Create: `desktop/src-tauri/src/firewall_helper.rs`
- Modify: `desktop/src-tauri/src/main.rs`
- Modify: `desktop/src-tauri/Cargo.toml`
- Test: Rust unit tests beside `firewall_helper.rs`

**Interfaces:**
- Allowed invocation: `AgentStateGuard.exe --asg-firewall-helper apply|remove --address <private-ipv4> --prefix <1..30>`.
- No other privileged verb or arbitrary command exists.

- [ ] **Step 1: Write RED Rust tests for the pure parser/command builder**

Tests cover:
- valid apply/remove arguments;
- missing/duplicate/unknown argument rejection;
- public, loopback, link-local, multicast and unspecified IP rejection;
- prefix outside 1..30 rejection;
- fixed Private-profile TCP and UDP port 8788 rules;
- fixed names, local address, and remote subnet;
- no profile-changing command.

- [ ] **Step 2: Run focused Rust tests and record RED**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml firewall_helper`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement strict early helper dispatch**

Implementation requirements:
- Parse the fixed helper mode before normal Tauri initialization.
- Re-run the existing read-only adapter/default-route/private-profile checks inside the elevated process.
- Require the supplied address/prefix to match the validated current physical default route.
- Reject VPN/virtual/loopback and Public/DomainAuthenticated profiles using existing Device Link policy.
- Execute direct `netsh.exe` process arguments, not `cmd.exe` or a shell.
- Apply fixed inbound TCP and UDP rules for port 8788, Private profile, local address, and remote subnet only.
- Remove only the two exact ASG-owned rule names.
- Return documented fixed exit codes.

- [ ] **Step 4: Run Rust tests and checks**

Run: `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check`

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml firewall_helper`

Run: `cargo check --manifest-path desktop/src-tauri/Cargo.toml`

Expected: PASS on a Rust/JDK-independent Windows toolchain.

- [ ] **Step 5: Commit**

```text
feat(desktop): 增加固定范围的 firewall UAC helper
```

---

### Task 3: Wire the trusted helper path and listener ordering

**Files:**
- Modify: `desktop/src-tauri/src/sidecar.rs`
- Modify: `agentguard/api/server.py`
- Modify: `agentguard/device_link/firewall.py`
- Modify: `agentguard/device_link/controller.py`
- Test: `tests/test_device_link_controller.py`
- Test: `tests/test_product_device_link_api.py`
- Test: Rust sidecar unit tests if present

- [ ] **Step 1: Write RED orchestration tests**

```python
def test_enable_never_starts_listener_when_elevation_declined(controller):
    result = controller.enable()
    assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_DECLINED"
    assert not controller.listener.running

def test_enable_starts_listener_only_after_scoped_rule_applied(controller):
    controller.enable()
    assert controller.calls == ["validate-network", "apply-firewall", "start-listener"]
```

Also assert:
- no manual PowerShell instruction is returned;
- removal failure disables listener but reports `DEVICE_FIREWALL_REMOVE_FAILED`/degraded state;
- helper environment is absent in non-Tauri direct Core runs and fails closed.

- [ ] **Step 2: Run focused tests and record RED**

Run: `pytest -q tests/test_device_link_controller.py tests/test_product_device_link_api.py -k "firewall or listener or elevation"`

Expected: FAIL at new reason/order assertions.

- [ ] **Step 3: Implement trusted path handoff**

Implementation requirements:
- Tauri obtains its own canonical `current_exe()` path and passes it through one dedicated environment variable to the child Core process.
- Core accepts that value only as the helper executable; no HTTP request can override it.
- Existing adapter selection remains authoritative.
- On Windows production, `ScopedFirewall` delegates apply/remove to the elevation runner.
- On tests/non-Windows, existing injection points remain usable without UAC.

- [ ] **Step 4: Enforce enable/disable ordering**

Implementation requirements:
- enable: validate network → apply firewall → bind/start TLS listener → publish enabled.
- apply failure: do not start listener, publish stable reason.
- listener failure after apply: attempt exact-rule cleanup and report cleanup uncertainty.
- disable: stop listener first → remove exact rules; remove failure remains visible as degraded/stale-rule risk.
- No change to private-LAN, TLS 1.3, disabled-by-default, pairing, or remote mutation semantics.

- [ ] **Step 5: Run targeted regression**

Run: `pytest -q tests/test_windows_firewall_elevation.py tests/test_device_link_firewall.py tests/test_device_link_controller.py tests/test_product_device_link_api.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```text
feat(device-link): 接入产品自有 scoped firewall UAC 路径
```

---

### Task 4: Contract, security, and MSI-path verification

**Files:**
- Modify: `docs/FRONTEND_BACKEND_CONTRACT.md`
- Modify: `docs/device-link/DESKTOP_SECURITY.md`
- Test: `tests/test_frontend_backend_contract.py`

- [ ] **Step 1: Freeze the security boundary in tests/docs**

Document and assert:
- ordinary user triggers a product-owned UAC prompt only when enabling/removing Device Link rules;
- no manual PowerShell/netsh prerequisite;
- no automatic profile switch;
- helper owns only TCP/UDP 8788 Private-profile rules on the validated physical subnet;
- UAC cancel/failure is fail-closed;
- workspace protection and recovery never require/administer UAC.

- [ ] **Step 2: Run static and unit verification**

Run: `pytest -q tests/test_frontend_backend_contract.py tests/test_windows_firewall_elevation.py tests/test_device_link_firewall.py tests/test_device_link_controller.py`

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml`

Run: `cargo check --manifest-path desktop/src-tauri/Cargo.toml`

Expected: PASS where toolchains are present; otherwise record `NOT_RUN` with the exact missing toolchain.

- [ ] **Step 3: Perform manual code audit**

Search for:
- `Set-NetConnectionProfile` (must not be newly introduced);
- arbitrary `cmd.exe`, `powershell -Command`, shell strings, or variable ports in helper mode;
- any UAC/elevation call under `agentguard/recovery` (must be absent);
- any frontend visual file changes (must be absent).

- [ ] **Step 4: Inspect diff and commit**

Run: `git diff --check`

```text
docs(security): 固化 Device Link firewall privilege boundary
```

---

## Completion evidence

- Python tests cover UAC accepted/declined/unavailable/nonzero results without real elevation.
- Rust tests cover the strict fixed helper surface and exact commands.
- Controller tests prove listener cannot run without scoped rules.
- Static audit proves no profile switch, generic elevated command, recovery UAC, or K3 UI change.
- Actual MSI/UAC interactive validation is reported separately; without it, `WINDOWS_FIREWALL_PRIVILEGE_PATH` cannot be marked fully verified.
