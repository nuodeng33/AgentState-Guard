# Fresh L4 Path — 2026-08-11

Document state: `DESIGN_ONLY`

Formal execution state: `NOT_RUN`

Frozen candidate: `f9731426e1d4433aeeb5ec50b6d887f705ab8b42`

Required order: `Windows L5 -> Fresh L4 -> L6 -> P9`

This document is the only deliverable of the fresh-path investigation. It does
not run L4, alter the frozen candidate, rehabilitate yesterday's host package,
or define a new evidence level, schema, collector, or general sandbox system.

## Authority and recorded evidence

The normative source is `docs/phases/09-acceptance-contract.md` at the frozen
candidate. It is marked `FROZEN`, explicitly supersedes contract commit
`6f646045267cf9573b4fed0d77e2c08ca09bc134`, and has no later superseding
contract in this checkout.

| Item | Identity |
| --- | --- |
| Frozen contract | SHA-256 `e1e97d95b1e70a0186277fd3fbcf1f532d5bda4738899bdc5cbca202c6391258`; Git blob `f0479e1c44d0e32b1b997b9e9e245e6cdce7c25b` |
| Companion readiness matrix | SHA-256 `694d9cbd50a98a09a273535d230394e56efe382075f1878520e3b58f6039586b`; Git blob `c57b8ffbc4a49d7d2f5b7d6f837f36b04a6c0acc` |
| Formal L4 evidence root | `/workspace/_reports/r4-p9-l4/f9731426e1d4433aeeb5ec50b6d887f705ab8b42/` |
| Evidence manifest | SHA-256 `d6c754817616bd11c69cc57b544332ebd081ce9ed9452f9a504a145624335326`; all 21 listed files reverified `OK` on 2026-08-11 |
| Formal report | SHA-256 `af9b5d1e6a294e7154fc129a999e9233a87ae1e676bd00bf207e9c2775097a7f` |
| Isolation primitive record | SHA-256 `fac0f862bb091d0eef8bc24f58067475f7493076b52bdbb56f64098e4fe400b4` |
| Partial-boundary record | SHA-256 `c652010e2e1eeeb9030806791e5205fcc9a6de6b5f1b35e6727609e21b53112b` |
| CI artifact ZIP | Artifact `9064190512`; SHA-256 `002973169a6acaf87f8a68ec4d61521c1b079cf8342febdb70221ea01303421a` |
| Exact wheel | SHA-256 `f04ccf5b4800239ac9c9b80a87e3ce8e8e665ee6d9e367f9002c3cea50414635`; embedded `PRODUCT_SHA` equals the frozen candidate |

The companion matrix is planning context, not an acceptance verdict. The
frozen contract remains authoritative if the two documents differ.

## Why previous L4 was correctly BLOCKED

The formal result was correctly `L4_BLOCKED`, reason
`L4_ENVIRONMENT_BLOCKED`. It was not a product failure and it was not an L4
execution result.

- `unshare` attempts for user, network, mount, and PID isolation returned
  `EPERM`.
- Bundled bubblewrap failed before starting its child process.
- The environment lacked `CAP_SYS_ADMIN` and `CAP_NET_ADMIN`, and supplied no
  usable container runtime, netfilter, BPF, or equivalent isolation primitive.
- `setpriv` did establish UID/GID 65534, zero capability sets, and
  `NoNewPrivs=1`, but the child retained the same network and mount namespaces.
- That partial child still saw `eth0`, a default route, and Docker's upstream
  resolver. Therefore external network denial was not established.
- A real loopback self-test succeeded only in that shared network namespace.
  Core was never started with external network denied.
- Exact-artifact lifecycle, hostile fixtures, offline AI behavior, truthful
  degradation, and the correlated MVP chain were consequently `NOT_RUN`.

This is precisely the frozen distinction between `BLOCKED` and `PASS`: a
partial identity restriction, a mock, a static test, or an artifact hash cannot
substitute for the L4 sandbox and correlated product execution.

## Why HOST_PACKAGE_INCOMPLETE must remain quarantined

The two evidence-only package roots remain quarantined and must not be run,
patched, copied, renamed, or used as the architecture for a fresh attempt:

- `/workspace/_handoff/r4-p9-l4-host/`
- `/workspace/asg-l4-isolation/_handoff/r4-p9-l4-host/`

They are divergent snapshots with no single authoritative entry point or
authoritative integrity manifest. Selected hashes identify, but do not validate,
the quarantined material:

| Quarantined file | SHA-256 |
| --- | --- |
| external `RUN_L4_HOST.ps1` | `b3d186d94dedaf06b6df112bd5dad09f88b6e0191cce4869ba281135f48a9880` |
| external `RUN_L4_HOST_FIXED.ps1` | `bc9af24b8162e2647459f280004ffe51246e36cd440832940469ed707e8c750f` |
| external `l4_runtime_runner.py` | `b7867820aeb53551ca2bfe9761307b7dfc29fc3ca279010394d838cfa8510837` |
| worktree-staging `RUN_L4_HOST.ps1` | `4cf8c9a444d2e64e736b9947d882374ce7829c7ae0ca99ea6b98dafcbec849eb` |
| worktree-staging `l4_runtime_runner.py` | `94750841459278aafbcde0ecd3ff8474e6ee9c6514da295ff328763c34ffd66a` |

Static inspection found the following disqualifying conditions:

- one staging composition placed Docker `--mount` arguments after the image
  and container command, where they would be application arguments rather than
  Docker options;
- neither package tree contains the required literal `--pull=never` control;
- one staging runner predeclared isolation and privilege `PASS` instead of
  deriving them from actual UID, capability, mount, route, socket, and write
  probes;
- the snapshots continued to drift after the initial review, so a later-added
  CLI/main cannot retroactively repair provenance or establish trust; and
- no version was executed successfully on the target Windows/Docker boundary.

The package therefore remains `HOST_PACKAGE_INCOMPLETE — DO NOT RUN`.

## What was unnecessary scope expansion

After the correct environment blocker was established, work expanded into a
Windows runner, image selection, evidence collection, manifest generation, and
verdict rendering. None was required to preserve or explain `L4_BLOCKED`.
Multiple implementations and duplicate entry points then increased the attack
surface and created fabricated-PASS risk.

The fresh path avoids that expansion. It defines one container boundary and
maps the existing frozen ACC requirements directly to future observations. It
does not design a reusable host package or evidence framework.

## Fresh minimal design

### Direct answer

The smallest candidate environment is:

`Windows real host -> Docker Desktop Linux engine -> one purpose-built, disposable L4 container`

The mechanism can objectively establish the frozen L4 envelope, provided every
pre-start and in-container probe below succeeds. This is a feasibility result,
not an L4 result. Docker documents that `--network none` creates only the
container loopback device, while `docker container create` provides native
controls for `--pull`, `--cap-drop`, `--user`, `--read-only`, `--security-opt`,
`--tmpfs`, and mounts:

- <https://docs.docker.com/engine/network/drivers/none/>
- <https://docs.docker.com/reference/cli/docker/container/create/>
- <https://docs.docker.com/reference/cli/docker/container/run>
- <https://docs.docker.com/desktop/features/wsl/>

These Docker references describe the candidate mechanism only. The frozen R4
contract defines acceptance.

### One-container boundary

Use exactly one newly created Linux container and one process tree for the
formal attempt. The host controls container creation and evidence retention;
the product process cannot access the Docker API.

The future `docker container create` option category must include, before the
image reference:

```text
--pull=never
--network none
--user <fixed-nonzero-uid>:<fixed-nonzero-gid>
--cap-drop ALL
--security-opt no-new-privileges=true
--read-only
--tmpfs /opt/asg-venv:rw,nosuid,nodev,uid=<uid>,gid=<gid>,mode=0700
--tmpfs /run/asg:rw,nosuid,nodev,noexec,uid=<uid>,gid=<gid>,mode=0700
--tmpfs /work/asg:rw,nosuid,nodev,noexec,uid=<uid>,gid=<gid>,mode=0700
--tmpfs /tmp:rw,nosuid,nodev,noexec,uid=<uid>,gid=<gid>,mode=0700
--mount type=bind,src=<exact-wheel>,dst=/opt/asg-input/candidate.whl,readonly
--mount type=bind,src=<frozen-scenario>,dst=/opt/asg-input/scenario,readonly
--mount type=bind,src=<fresh-evidence>,dst=/evidence
<locally-present-image-by-content-identity>
<one-shot-acceptance-entrypoint>
```

There must be no `--privileged`, `--cap-add`, host network, published port,
Docker socket/API mount, source checkout mount, broad host-directory mount, or
mutable product input. All Docker options and mounts must precede the image.

The mount allowlist is fixed before execution; it is not inferred from whatever
the container happens to expose:

| Container path | Mount kind / filesystem | Required options | Allowed writable range |
| --- | --- | --- | --- |
| `/` | pinned image rootfs / engine snapshot | `ro` | none; prerequisite runtime only |
| `/opt/asg-input/candidate.whl` | single-file host bind | `ro` | none; hash and `PRODUCT_SHA` checked before and after |
| `/opt/asg-input/scenario/` | host directory bind | `ro` | none; hashes checked before and after |
| `/opt/asg-venv/` | `tmpfs` | `rw,nosuid,nodev,uid=<uid>,gid=<gid>,mode=0700` | fixed UID/GID; non-editable wheel installation only |
| `/run/asg/` | `tmpfs` | `rw,nosuid,nodev,noexec,uid=<uid>,gid=<gid>,mode=0700` | fixed UID/GID; Core state and in-run Ledger only |
| `/work/asg/` | `tmpfs` | `rw,nosuid,nodev,noexec,uid=<uid>,gid=<gid>,mode=0700` | fixed UID/GID; isolated change and test-restore targets only |
| `/tmp/` | `tmpfs` | `rw,nosuid,nodev,noexec,uid=<uid>,gid=<gid>,mode=0700` | fixed UID/GID; bounded temporary files only |
| `/evidence/` | fresh host directory bind | `rw` | raw evidence only; never product input or authority |
| `/proc` | `proc` | `rw,nosuid,nodev,noexec` | kernel-generated process state only; product writes to privileged entries must deny |
| `/proc/bus`, `/proc/fs`, `/proc/irq`, `/proc/sys`, `/proc/sysrq-trigger` | `proc` submounts | `ro,nosuid,nodev,noexec` | none |
| Docker default sensitive `/proc/*` and `/sys/*` mask set | masked bind or empty `tmpfs` | inaccessible or `ro` | none; exact destinations frozen from container inspect before start |
| `/dev` | `tmpfs` | `rw,nosuid,mode=0755` | standard Docker device nodes only; no new node or regular file |
| `/dev/pts` | `devpts` | `rw,nosuid,noexec` | none; formal run allocates no TTY |
| `/dev/shm` | `tmpfs` | `rw,nosuid,nodev,noexec`, bounded size | none; empty before and after |
| `/dev/mqueue` | `mqueue` | `rw,nosuid,nodev,noexec` | none; empty before and after |
| `/sys` | `sysfs` | `ro,nosuid,nodev,noexec` | none |
| `/sys/fs/cgroup` | `cgroup2` | `ro,nosuid,nodev,noexec` | none |
| `/etc/hostname`, `/etc/hosts`, `/etc/resolv.conf` | Docker-managed single-file binds | only these three destinations may surface `rw`; record underlying filesystem and options | zero product mutation; before/after hashes identical |

For the system rows, listed options are mandatory security subsets; extra
security-restricting flags or masked/read-only submounts are permitted. Missing
required flags, another writable destination, or a broader writable range is
a failed envelope. A technically writable system mount does not authorize
product state there: every row's writable-range condition must also hold.

No other host bind, volume, tmpfs, device, or writable data path is allowed.
Before start, compare the configured rootfs, binds, and tmpfs mounts to the
first eight rows. After start, compare every mount destination, filesystem type,
security-option subset, and writable range to all rows. An extra writable
destination, writable image/input mount, mutation of a Docker-managed identity
file, or unexplained mount is a failed envelope; do not waive it as Docker
default behavior.

The purpose-built image is a prerequisite image, not the candidate artifact.
It must already exist locally, be recorded by immutable image ID/digest, and
contain Python 3.11 plus the wheel's runtime dependencies. It must not contain
the AgentState Guard source checkout or candidate installation. The exact
candidate wheel is installed during the formal run, after network denial is
proved, using a non-editable, local-only install into tmpfs.

The one-shot acceptance entrypoint is a bounded future test input. It drives the
installed wheel's real CLI/API/service surfaces and writes raw observations. It
must be independently reviewed and content-hashed before formal L4. It is not a
general runner framework, and no implementation is created by this document.

The frozen contract does not literally require a separate Linux user namespace
or every capability field to be zero. It requires an isolated sandbox,
non-root execution, restricted permissions, and mount/capability proof. This
design conservatively requires a separate container mount/network/PID boundary,
`CapEff=0`, zero permitted/bounding/ambient capabilities, and
`NoNewPrivs=1`. Those are measured predicates, never inferred from requested
flags.

### Mechanical ACC evidence matrix

| ACC requirement | Future concrete proof | Exact command/probe category | PASS condition | BLOCK/FAIL condition |
| --- | --- | --- | --- | --- |
| L4 envelope and immutable run identity | Exact candidate, container/image identity, start/end UTC, toolchain, complete commands/exits, mounts, namespaces, identity, limitations | Host identity and `docker version/info/image inspect/container inspect`; inside `/proc/self/ns/*`, `/proc/self/status`, `/proc/self/mountinfo`; input hashes before/after | One fresh container and one exact-SHA trace contain every frozen evidence-envelope field | Missing primitive or immutable input is `BLOCKED`; missing observation is `NOT_RUN/NOT_VERIFIED`; contradiction is `FAIL` |
| ACC-001 external offline plus loopback | No default/external route; DNS, fixed-IP TCP, HTTPS, Provider, GitHub and PyPI attempts denied; loopback listener and Core succeed in the same namespace | `/proc/net/route`, interface inventory, real DNS/TCP/HTTPS denial probes, `127.0.0.1` bind/connect, Core health/session/readiness | Every external probe is denied and Core loopback path succeeds without remote dependency | Any external success or product fetch/dependency is `FAIL`; unavailable `--network none` or failed loopback primitive is `BLOCKED` |
| ACC-002 non-root and real permission denial | Entire product path stays at fixed nonzero UID/GID; real root-owned unreadable/unwritable fixtures degrade truthfully | `id`, `/proc/self/status`, process inventory, read/write probes against rootfs/input/denied fixture and allowed tmpfs | Non-root identity is stable, denied operations really fail, required product path completes or returns contract-approved degradation | UID 0, identity transition, simulated permission error, crash, or false SAFE is `FAIL`; inability to create the identity is `BLOCKED` |
| ACC-003 zero capabilities, no escalation, no Docker API | Zero capability sets, NNP, read-only root, bounded mounts, no socket/daemon environment, product still completes | `CapInh/Prm/Eff/Bnd/Amb`, `NoNewPrivs`, mount inventory, known socket path checks and connection denial, `DOCKER_HOST`/mount inspection | All capability fields are zero, NNP is 1, no usable Docker endpoint or escalation path exists | Usable socket/API, nonzero effective/permitted/bounding capability, writable root, or privilege gain is `FAIL` |
| ACC-004 dirty/hostile fixtures | Bounded untracked, modified, stale, malformed, traversal, permission-denied, and conflicting-evidence cases; unrelated hashes preserved; stable reasons; leak scan clean | Frozen fixture hash list; before/after file hashes; real product requests/commands; reason-code and sanitized-output checks | Every named case executes, rejects ambiguity, preserves unrelated content, and leaks no sensitive fixture | Missing fixture is `NOT_RUN`; ambiguous authority, collateral modification, unstable reason, or leak is `FAIL` |
| ACC-005 runtime discovery | Verified allowlisted runtime/probe Ledger events and projections for available, empty, unreachable, stale, conflicting, and invalid-ledger states | Real discovery through controlled-change prepare; authenticated runtime projection; pre-projection Ledger verification; bounded negative state inputs | Only proven runtime is available; empty/unreachable/stale/conflicting facts remain `EMPTY/UNKNOWN/DEGRADED` | Any unproven `AVAILABLE/RUNNING`, including reproduced `PROBE_UNREACHABLE -> AVAILABLE`, is `FAIL` |
| ACC-006 agent discovery | Real current-process evidence, allowlisted `AGENT_DETECTED`, bounded confidence, unique runtime/workspace binding, ambiguous negatives | Controlled-change prepare; authenticated agent projection; process identity and event-reference comparison | Projected agent is derived from verified process/Ledger facts and ambiguity fails closed | Static/caller claim becomes live authority, confidence escapes bounds, or ambiguous binding is accepted: `FAIL` |
| ACC-007 workspace binding | One server-owned execution-domain/workspace/scope binding with no caller path authority; stale/cross/conflicting negatives | Inspect `WORKSPACE_LINKED` and policy activation context; submit caller-path and mismatched-binding cases | Exactly one verified binding controls mutation; missing/conflicting binding prevents it | Caller/raw path authority or mutation without current unique binding is `FAIL` |
| ACC-008 deterministic policy | Durable write-before `POLICY_EVALUATED` and precedence `BLOCK > REVIEW > UNKNOWN > ALLOW` | Prepare real changes for REVIEW plus bounded BLOCK/UNKNOWN/collision inputs; inspect session and Ledger before any write | BLOCK/UNKNOWN never execute, REVIEW waits, and checkpoint requirement remains | AI/caller/approval weakens policy or any write precedes the durable outcome: `FAIL` |
| ACC-009 AI offline fallback | Both no-AI-required and AI-required-but-unavailable paths under the already-proven offline namespace; deterministic state unchanged; bounded synthetic sensitive canaries remain sanitized | Provider denial from real network isolation; prepare path with no provider and unreachable provider; compare policy/session/Ledger before/after; inspect existing provider-request construction, AI event/output, and log surfaces and scan them for the canaries | Core survives; BLOCK remains BLOCK, UNKNOWN remains UNKNOWN, REVIEW remains REVIEW; no fabricated AI event/fact; provider-bound input and every AI output/event omit or redact the sensitive canaries | Crash, observable unsanitized provider input/output/event, or AI absence/error/output creating facts, ALLOW, approval, authority, or trust is `FAIL`; no observable sanitization proof is `NOT_VERIFIED` |
| ACC-010 explicit approval | One current same-session action consumed once; exactly one `USER_APPROVED`; no checkpoint/activation/policy promotion at approval | Authenticated approval through real API plus replay, stale, malformed, cross-session, terminal, recovery-bound, and concurrent requests | Only the current action succeeds once and remains REVIEW/APPROVED pending checkpoint | Replay or wrong binding succeeds, event count differs, checkpoint is created, or policy becomes ALLOW: `FAIL` |
| ACC-011 checkpoint binding | Pre-change checkpoint bound to session, workspace, scope, exact SHA, immutable manifest; created is not recoverable | Apply approved change; inspect checkpoint/manifest/event bindings; missing/stale/corrupt/mismatch negatives | Valid bound checkpoint and manifest precede activation/change; invalid evidence stops | Activation/change with absent or mismatched checkpoint, or `CHECKPOINT_CREATED` promoted to recoverable: `FAIL` |
| ACC-012 controlled change | Exactly approved scope changes atomically through authoritative path with all bindings | Real `/changes`, approval, and `/apply` sequence; scope-drift, replay, reorder, replacement, partial-write and interruption cases | Only intended config content changes; failures stop or roll back without partial effect | Out-of-scope/partial/unbound mutation or non-atomic failure is `FAIL` |
| ACC-013 post-change diff | Bound authoritative before/after digests and diff account for every in-scope effect, bind to the same checkpoint, and preserve hostile unrelated files | Compare API result, durable change event, checkpoint ID and manifest digest, target hashes, unrelated fixture hashes, and diff digest | Diff is complete, scope- and checkpoint-bound, matches the checkpoint manifest and actual files, and leaves unrelated hashes identical | Missing/inconsistent/out-of-scope or checkpoint-mismatched diff, or collateral change, is `FAIL` |
| ACC-014 offline verification | Local verification occurs after diff, stays network-independent, and is bound to session/change/diff | Inspect verification result/order/bindings and commands/exits; inject failed, missing, stale, wrong-scope verifier results | Verification passes after diff using only local immutable inputs; failures prevent completion | Skipped/networked/stale/wrong-scope/failed verification followed by completion is `FAIL` |
| ACC-015 isolated test-restore (L4 portion) | Test restore writes only to a distinct tmpfs destination and proves content/metadata without production-state writes or trust promotion | Invoke installed wheel's real recovery test-restore path against the same bound checkpoint; before/after production and destination hashes/modes; cleanup result | Destination content/metadata match, production target/state remain unchanged, and result stays within L4 recovery claims | Production write, mismatch, missing isolation, cleanup failure, or recoverability/trust promotion is `FAIL`; L6 fault drill is not part of this run |
| ACC-016 Ledger verification | Verification before authority projection and after final event; complete ordered hash-linked range bound to session/artifacts | Installed wheel's `verify_ledger()` over the same database; record first/last refs, count, final hash; bounded corrupt/missing/duplicate/reorder/unbound cases | Both checks return no errors and one correlation accounts for all required events | Invalid Ledger accepted or cross-run/unbound evidence used is `FAIL` |
| ACC-017 exact wheel and offline non-editable install | Exact CI wheel hash/provenance, clean local-only install, dependency and entry-point smoke, import origin inside installation | Host and inside SHA-256/embedded PRODUCT_SHA; `pip install --no-index --no-deps` into fresh tmpfs environment; dependency/import/entry-point checks | Hash and PRODUCT_SHA match; no editable metadata/source path; all imports and entry point resolve from installed artifact | Hash/provenance mismatch is `FAIL`; missing local image dependency is `BLOCKED`; any network fetch or source import is `FAIL` |
| ACC-022 one unspliced correlation | Runtime -> agent -> workspace -> policy -> AI/bypass -> approval -> checkpoint -> change -> diff -> verify -> test-restore -> final Ledger check in one container/database/session-linked chain | One correlation ID; ordered API/service trace; event/artifact reference reconciliation | No step is skipped, simulated, or borrowed from another run/SHA/environment | Separate-run splice, helper-generated success, missing step, or caller authority is `FAIL/NOT_VERIFIED` |

The full-chain row is decisive: passing only network probes, `/api/health`, or
component tests cannot pass frozen L4.

## Required host prerequisites

All prerequisites are gates, not evidence of PASS:

1. Windows L5 for the same frozen candidate has completed with its own formal
   result before any L4 start.
2. Docker Desktop is running in Linux-container mode on the named Windows real
   host. Host/Windows/Docker/engine versions and backend are recorded.
3. The engine demonstrably accepts `--pull=never`, `--network none`, explicit
   non-root user, `--cap-drop ALL`, `no-new-privileges`, read-only rootfs,
   tmpfs, and read-only file mounts.
4. One purpose-built dependency image already exists locally and is pinned by
   image ID/digest. It contains Python 3.11 and all declared runtime
   dependencies, but no candidate/source checkout.
5. The exact artifact ZIP/wheel and producing CI metadata above are available
   locally. No historical or source-built replacement is allowed.
6. The bounded scenario input and hostile fixtures are frozen, reviewed, and
   content-hashed before formal execution.
7. A new evidence directory outside product state is empty and writable only
   as required. Product state and mutable workspace live only on named tmpfs
   mounts.
8. No existing formal container, including `agent-dev`, is selected, changed,
   stopped, or mounted.

The exact CI artifact contains the application wheel, not a dependency
wheelhouse. Therefore the locally present, content-identified dependency image
is a hard prerequisite. If satisfying a dependency would require `docker pull`,
`pip` network access, or another download after formal start, the result is
`BLOCKED`; execution must not continue.

## Exact future execution sequence

This sequence starts only after Windows L5 has finished.

1. **Freeze identity.** Record the Windows host, standard-user context, Docker
   Desktop/engine/backend, exact candidate, CI run/job/artifact IDs, wheel hash,
   image ID/digest, scenario/fixture hashes, UTC start time, and fresh output
   directory. Abort on any mismatch.
2. **Prove local prerequisites.** Use local inventory/inspect only. Confirm the
   image is already present and dependencies are declared. Do not pull, build,
   install, or fetch from a network during the formal run.
3. **Create exactly one new container.** Use the option category above, with
   `--pull=never`; put every option/mount before the image; publish no port; use
   no host network, privilege, capability add, Docker socket, source checkout,
   or broad host mount.
4. **Inspect before start.** Mechanically compare `docker inspect` with the
   required network mode, user, capability drop, security option, read-only
   rootfs, tmpfs, exact mount allowlist, image ID, and command. Any difference
   is `BLOCKED`; do not start.
5. **Prove the live envelope before product start.** Inside the same container,
   record namespaces, UID/GID/groups, capability sets, NNP, mounts, routes,
   interfaces, resolver text, Docker endpoint absence, denied root/input writes,
   and allowed tmpfs/evidence writes. Run real external denial and loopback
   probes. Requested flags are not results.
6. **Install only the exact artifact.** With the network already denied, create
   a clean tmpfs environment and install the mounted wheel non-editably using
   local-only/no-dependency-fetch options. Verify dependencies, entry point,
   import origin, wheel metadata, embedded PRODUCT_SHA, and unchanged input
   hash. Source-tree import is forbidden.
7. **Start and restart Core on loopback.** Start the installed entry point bound
   only to `127.0.0.1`; exercise health, session/bootstrap, and readiness;
   perform normal shutdown and one restart without relaxing the envelope. Do
   not accept an authoritative projection before the next step's pre-projection
   Ledger verification.
8. **Run one positive frozen correlation.** In one writable tmpfs workspace and
   one durable-in-run database, create the required discovery facts, record a
   successful `verify_ledger()` immediately before the first authenticated
   authoritative projection, then execute runtime/agent projection, workspace binding,
   deterministic policy, the required offline AI outcome, one explicit
   approval, bound checkpoint, controlled change, bound diff, offline
   verification, and isolated test-restore. After the final event, record a
   second successful `verify_ledger()`. Preserve the same correlation and
   references through all steps.
9. **Run the frozen negative cases.** Within the same still-isolated container,
   execute the named ACC-004 through ACC-016 negative fixtures. Keep the
   positive correlation separate from negative state, but do not splice any
   success across runs. Re-check external denial and Core liveness after
   provider/detector failures.
10. **Close without inference.** Shut Core down, record exit results, final
    Ledger result, before/after hashes, container post-state, UTC end time, and
    all deviations. Remove only the newly named disposable container after raw
    evidence is safely retained. Derive the verdict from the frozen criteria;
    never prewrite PASS.

## Evidence to capture

Use the frozen contract's evidence envelope directly. For every applicable ACC
record:

- requirement and gate IDs;
- exact 40-character SHA and branch/detached state;
- tracked/untracked state relevant to the artifact;
- UTC start and end timestamps;
- Windows host, Docker engine/backend, image, runtime, UID/GID, permissions,
  namespace, capability, mount, and network identity;
- complete method/command list, exit results, test/sample counts, and sanitized
  raw logs or log hashes;
- artifact/input/output names, sizes, SHA-256 hashes, producing job conclusion,
  and before/after input hashes;
- one correlation's ordered event/artifact references and pre/post Ledger
  verification; and
- limitations and deviations, including an explicit empty value when none.

No new evidence level or schema is introduced. A Docker flag, inspect document,
prose report, helper assertion, or artifact hash alone is not an L4 result.

## Abort conditions

Abort before product execution and report `BLOCKED` when:

- Windows L5 has not completed in the required order;
- candidate, wheel, embedded PRODUCT_SHA, CI artifact, scenario input, fixture,
  or image identity is missing or mismatched;
- the local image is absent, Docker would pull, or any dependency requires a
  network fetch;
- Docker Desktop cannot create or inspect the exact required container
  configuration;
- a mount outside the allowlist appears, the Docker socket/API is exposed, or
  non-root/tmpfs/evidence permissions cannot be established; or
- external network denial and same-namespace loopback cannot both be proved.

After valid execution begins, record `FAIL`, not `BLOCKED`, for an objective
product contradiction. Examples include a crash, fabricated SAFE/AVAILABLE,
policy weakening, unauthorized mutation, recovery/trust promotion, collateral
file change, or invalid Ledger acceptance. In particular, yesterday's
`STATIC_ONLY_NOT_RUNTIME_FINDING` risk—only `PROBE_UNREACHABLE` evidence
producing top-level `AVAILABLE`—becomes an ACC-005 runtime failure only if
reproduced. If a required case is simply not run, use `NOT_RUN`; insufficient
provenance is `NOT_VERIFIED`.

No WSL/Linux VM fallback is proposed. The selected Docker Desktop mechanism can
express every explicit frozen L4 envelope predicate. Failure of f973 behavior
inside that valid envelope would be `L4_FAIL`, not a reason to change the
environment or weaken the contract.

## What must NOT be claimed

- `L4 PASS`: `NOT CLAIMED`; formal Fresh L4 is `NOT_RUN`.
- `L5 PASS`: not established by this document; wait for the independent Windows
  L5 result.
- `L6 PASS`: `NOT RUN` and out of scope.
- `P9 PASS` / `P9 COMPLETE`: `NOT CLAIMED`.
- `R4 COMPLETE`: `NOT CLAIMED`.
- `PRODUCT READY`: `NOT CLAIMED`.
- Docker feasibility is not product acceptance.
- The quarantined host package is not repaired, accepted, or reusable.
- L4 test-restore is not L6 recoverability, `CHECKPOINT_CREATED` is not
  recoverable, and `R3` is not `TRUSTED`.

## Next action

`WAIT FOR WINDOWS L5`.

After L5 finishes, begin a fresh L4 implementation from this contract mapping,
not from either quarantined package tree.
