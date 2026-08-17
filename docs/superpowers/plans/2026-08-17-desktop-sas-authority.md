# Desktop SAS Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 loopback Desktop projection 取得与 Android pairing flow 完全相同的 server-owned SAS，并确保 frontend 不计算、猜测或持有 SAS authority。

**Architecture:** `PairingSession` 在既有 transcript-derived `start_sas()` 内生成并保存 SAS；Device Link gateway 仅在 `SAS_PENDING` 状态向 Core-owned loopback projection 暴露该值。Android 继续读取既有 `/device/v1/pair/{session_id}/sas`，Desktop 继续读取既有 Core pairing status endpoint。terminal state 立即清除 SAS 与 pairing secret。

**Tech Stack:** Python 3.11+, FastAPI, existing Device Link pairing state machine, pytest.

## Global Constraints

- SAS 唯一 authority 是 Core/server-owned pairing state。
- 不允许 Desktop、React、Android 或 caller 自行计算 SAS。
- 不新增 remote mutation，不改变 pairing transcript、TLS fingerprint、ticket、expiry 或 confirm semantics。
- SAS 只在 `SAS_PENDING` 暴露；完成、取消、过期、失败后必须清除。
- 不修改 K3 presentation；只补兼容字段和 contract tests。
- 每个 production change 前先运行对应 RED test。

---

## File map

- Modify `agentguard/device_link/pairing.py`: 保存受状态约束的 server-owned SAS，并在 terminal transition 清除。
- Modify `agentguard/device_link/gateway.py`: `pair_poll()` 仅在 `SAS_PENDING` 返回 SAS。
- Modify `agentguard/api/product_device_link.py`: allowlist `sas` 到 loopback pairing status projection。
- Test `tests/test_device_link_pairing.py`: transcript-derived SAS、状态边界、terminal cleanup。
- Test `tests/test_device_link_gateway.py`: Android SAS endpoint 与 Desktop poll 值一致。
- Test `tests/test_product_device_link_api.py`: loopback Core projection 输出/隐藏 SAS。
- Modify `docs/FRONTEND_BACKEND_CONTRACT.md`: 记录 server-owned optional `sas` 字段及状态约束。

---

### Task 1: Persist SAS inside pairing authority

**Files:**
- Modify: `agentguard/device_link/pairing.py`
- Test: `tests/test_device_link_pairing.py`

**Interfaces:**
- `PairingSession.start_sas() -> str` remains the only SAS generator.
- `PairingSession.sas_for_projection() -> str | None` exposes only an already-generated authority value.

- [ ] **Step 1: Write RED lifecycle tests**

```python
def test_start_sas_persists_transcript_derived_value(session):
    sas = session.start_sas()
    assert session.sas_for_projection() == sas
    assert len(sas) == 6

def test_sas_is_not_projected_outside_sas_pending(session):
    assert session.sas_for_projection() is None
    session.start_sas()
    session.cancel()
    assert session.sas_for_projection() is None
```

- [ ] **Step 2: Run the focused test and record RED**

Run: `pytest -q tests/test_device_link_pairing.py -k "sas_for_projection or persists_transcript"`

Expected: FAIL because no stored SAS/projection accessor exists.

- [ ] **Step 3: Implement the minimum state-owned field**

Implementation requirements:
- Initialize `_sas: str | None = None` with pairing secret state.
- `start_sas()` computes exactly once from the existing transcript implementation, stores it, and returns it.
- Re-entry never generates a second different SAS for the same session.
- `sas_for_projection()` checks current state and returns only during `SAS_PENDING`.
- Every terminal transition uses one cleanup method that clears `_sas` and the existing secret material.
- Never log or append the SAS to Evidence Ledger.

- [ ] **Step 4: Run focused and full pairing tests**

Run: `pytest -q tests/test_device_link_pairing.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
feat(device-link): 固化 server-owned SAS 生命周期
```

---

### Task 2: Project the same SAS to Android and loopback Desktop

**Files:**
- Modify: `agentguard/device_link/gateway.py`
- Modify: `agentguard/api/product_device_link.py`
- Test: `tests/test_device_link_gateway.py`
- Test: `tests/test_product_device_link_api.py`

**Interfaces:**
- Android keeps `GET /device/v1/pair/{session_id}/sas`.
- Desktop keeps `GET /api/v1/device-link/pairings/{session_id}`.
- Desktop response adds optional `sas` only when pairing state is `SAS_PENDING`.

- [ ] **Step 1: Write RED cross-projection tests**

```python
def test_android_and_desktop_projection_share_server_sas(gateway, session_id):
    android = gateway.pair_sas(session_id)
    desktop = gateway.pair_poll(session_id)
    assert desktop["sas"] == android["sas"]

def test_product_pairing_status_hides_sas_after_terminal(client, session_id):
    _complete_pairing(session_id)
    body = client.get(f"/api/v1/device-link/pairings/{session_id}").json()
    assert "sas" not in body
```

- [ ] **Step 2: Run focused tests and record RED**

Run: `pytest -q tests/test_device_link_gateway.py tests/test_product_device_link_api.py -k "sas"`

Expected: FAIL because `pair_poll()` does not project SAS.

- [ ] **Step 3: Implement the minimum gateway/API projection**

Implementation requirements:
- `pair_poll()` asks the session for `sas_for_projection()`; it never recomputes.
- Add `sas` only when non-`None`; terminal and pre-SAS responses omit it.
- Product API copies only the allowlisted value from gateway output.
- Preserve existing 404/expired/error envelopes and auth boundaries.

- [ ] **Step 4: Run Device Link targeted regression**

Run: `pytest -q tests/test_device_link_pairing.py tests/test_device_link_gateway.py tests/test_product_device_link_api.py tests/test_device_link_api.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
feat(api): 向 Desktop 投影 authority SAS
```

---

### Task 3: Freeze and verify the SAS contract

**Files:**
- Modify: `docs/FRONTEND_BACKEND_CONTRACT.md`
- Test: `tests/test_frontend_backend_contract.py`

- [ ] **Step 1: Add contract assertions**

Assert that the contract states:
- `sas` is server-owned and optional.
- It is present only in `SAS_PENDING`.
- Desktop and Android compare the same exact value.
- Frontend must not derive it.

- [ ] **Step 2: Update the contract without UI changes**

Document the response field only; do not change navigation, presentation, i18n, React, or Compose.

- [ ] **Step 3: Run contract and Device Link regression tests**

Run: `pytest -q tests/test_frontend_backend_contract.py tests/test_device_link_pairing.py tests/test_device_link_gateway.py tests/test_product_device_link_api.py`

Expected: PASS.

- [ ] **Step 4: Inspect diff and commit**

Run: `git diff --check`

```text
docs(contract): 固化 Desktop SAS authority projection
```

---

## Completion evidence

- Desktop and Android values are asserted equal after one server computation.
- Pre-SAS and terminal states do not expose SAS.
- Terminal cleanup is covered for complete/cancel/expire/fail paths supported by the state machine.
- No frontend file changed.
- No new endpoint or mutation was introduced.
