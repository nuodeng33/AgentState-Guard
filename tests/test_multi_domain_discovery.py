"""Multi-domain discovery: Docker/WSL observed read-only from the host.

Positive: same supported known identities inside observable execution
domains (docker top rows). Negative: generic runtimes, unrelated commands
carrying agent-looking arguments, and unclassified executables (zcode)
stay out. Domain failure: docker daemon unreachable degrades truthfully
and never erases host-native discovery.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agentguard.core.runner import CommandResult
from agentguard.discovery import (
    CapabilityStatus,
    DiscoverySnapshot,
    ExecutionDomainKind,
)
from agentguard.discovery.domains.host_domains import (
    HostDomainObservation,
    observe_docker_domains,
    observe_wsl_domains,
)
from agentguard.discovery.product import (
    ProductDiscoveryService,
    _admit_container_process,
)
from agentguard.discovery.workspace_authority import resolve_host_workspace

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _result(stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(returncode, stdout, "")


PS_OUTPUT = "abc123def456\tmy-runner\tUp 3 hours\n"

#: Real-shaped docker inspect bounded line: WorkingDir \t StartedAt \t mounts.
INSPECT_MOUNTED = (
    "/workspace\t2026-08-20T10:00:00Z\tsha256:helper-image\t"
    "volume|agent-workspace|/workspace|true;"
    "bind|/var/run/sock|/var/run/sock|true;\n"
)
INSPECT_AMBIGUOUS = (
    "/data/sub\t2026-08-20T10:00:00Z\t"
    "volume:one=>/data;volume:two=>/data/sub;\n"
)
INSPECT_BLOCKED_ROOT = (
    "\t2026-08-20T10:00:00Z\t"
    "overlay:x=>/;volume:agent-workspace=>/workspace;\n"
)


def _bind_inspect(source, *, started_at="2026-08-20T10:00:00Z", writable=True):
    return (
        f"/workspace/project/sub\t{started_at}\t"
        f"bind|{source}|/workspace/project|{str(writable).lower()};\n"
    )


class TestDockerDomainObservation:
    def _runner_with_top(self, top_stdout: str, top_rc: int = 0, inspect: str = INSPECT_MOUNTED):
        def runner(cmd: list[str]) -> CommandResult:
            if cmd[0:2] == ["docker", "ps"]:
                return _result(PS_OUTPUT)
            if cmd[0:2] == ["docker", "top"]:
                return _result(top_stdout, top_rc)
            if cmd[0:2] == ["docker", "inspect"]:
                return _result(inspect)
            if cmd[0:2] == ["docker", "info"]:
                return _result("engine-fixture")
            if cmd[0:3] == ["docker", "volume", "inspect"]:
                return _result(
                    "agent-workspace\tlocal\tlocal\t"
                    "2026-07-28T13:33:50Z\t/var/lib/docker/volumes/agent-workspace/_data"
                )
            raise AssertionError(f"unexpected command {cmd}")

        return runner

    def test_known_identities_inside_container_are_admitted(self):
        top = (
            "PID ARGS\n"
            "11 node /usr/local/lib/node_modules/@openai/codex/bin/codex.js\n"
            "12 /usr/local/bin/claude\n"
            "13 node /usr/lib/node_modules/@moonshot-ai/kimi-code/cli.js\n"
        )
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(top),
            presence=lambda: True,
        )
        assert [d.kind for d in observation.domains] == [
            ExecutionDomainKind.CONTAINER,
        ]
        agents = sorted(a.agent_type for a in observation.agents)
        assert agents == ["CLAUDE", "CODEX", "KIMI_CODE"]
        # Agents belong to the container domain, never the host domain.
        for agent in observation.agents:
            assert agent.domain_id.startswith("docker-container-")
        # The real mount-backed workspace binds every identified agent.
        assert len(observation.workspaces) == 1
        workspace = observation.workspaces[0]
        assert workspace.domain_id.startswith("docker-container-")
        assert workspace.agent_ids == tuple(
            sorted(agent.agent_id for agent in observation.agents)
        )
        for agent in observation.agents:
            assert agent.workspace_ids == (workspace.workspace_id,)
        workspace_evidence = [
            e for e in observation.evidence if e.fact_type == "workspace.present"
        ]
        assert len(workspace_evidence) == 1
        assert workspace_evidence[0].value["root"] == "/workspace"
        assert workspace_evidence[0].value["workspace_kind"] == "SANDBOX_VOLUME"
        assert workspace_evidence[0].value["container_started_at"] != ""
        runtimes = {r.runtime_type for r in observation.runtimes}
        assert runtimes == {"CONTAINER_RUNTIME"}
        facts = [e.fact_type for e in observation.evidence]
        assert "runtime.metadata" in facts and "agent.metadata" in facts

    def test_real_dogfood_processes_are_admitted_without_harness_overclaim(self):
        top = (
            "PID ARGS\n"
            "11 kimi-code\n"
            "12 ttyd -W -p 7681 -t enableZmodem=true -c user:redacted "
            "/usr/local/bin/codex-main\n"
            "13 node /usr/local/bin/cloudcli\n"
            "14 node server.js --label /usr/local/bin/cloudcli\n"
            "15 cat /usr/local/bin/codex-main\n"
        )
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(top),
            presence=lambda: True,
        )

        assert sorted(agent.agent_type for agent in observation.agents) == [
            "CLOUDCLI",
            "KIMI_CODE",
        ]

    def test_idle_terminal_harness_is_not_a_running_codex_agent(self):
        top = (
            "PID ARGS\n"
            "11 ttyd -W -p 7681 /usr/local/bin/codex-main\n"
            "12 tmux new-session -d -s codex-main -c /workspace\n"
            "13 bash -bash\n"
        )
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(top),
            presence=lambda: True,
        )

        assert observation.agents == ()

    def test_one_codex_process_tree_projects_one_agent_instance(self):
        top = (
            "PID PPID ARGS\n"
            "31 7 node /usr/local/lib/node_modules/@openai/codex/bin/codex.js\n"
            "32 31 /opt/@openai/codex/vendor/bin/codex\n"
        )
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(top),
            presence=lambda: True,
        )

        assert [(agent.agent_type, agent.agent_id) for agent in observation.agents] == [
            (
                "CODEX",
                "external-agent-0b3b4741be6e524734a57444",
            )
        ]

    def test_resolved_docker_executable_is_used_for_every_observer_stage(self):
        resolved = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
        calls: list[list[str]] = []

        def runner(cmd: list[str]) -> CommandResult:
            calls.append(cmd)
            assert cmd[0] == resolved
            if cmd[1] == "ps":
                return _result(PS_OUTPUT)
            if cmd[1] == "top":
                return _result("PID ARGS\n11 kimi-code\n")
            if cmd[1] == "inspect":
                return _result(INSPECT_MOUNTED)
            if cmd[1] == "info":
                return _result("engine-fixture")
            if cmd[1:3] == ["volume", "inspect"]:
                return _result(
                    "agent-workspace\tlocal\tlocal\t"
                    "2026-07-28T13:33:50Z\t"
                    "/var/lib/docker/volumes/agent-workspace/_data"
                )
            raise AssertionError(f"unexpected command {cmd}")

        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=runner,
            presence=lambda: True,
            docker_executable=resolved,
        )

        assert [command[1] for command in calls] == [
            "ps",
            "top",
            "inspect",
            "info",
            "volume",
        ]
        assert [agent.agent_type for agent in observation.agents] == ["KIMI_CODE"]

    def test_generic_and_unrelated_processes_stay_out(self):
        top = (
            "PID ARGS\n"
            "21 node server.js\n"
            "22 python3 -m http.server\n"
            "23 cat node_modules/@openai/codex/README.md\n"
            "24 npm run build\n"
            "25 zcode --serve\n"
            "26 node /usr/local/bin/zcode\n"
            "27 vite build\n"
        )
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(top),
            presence=lambda: True,
        )
        assert observation.agents == ()

    def test_absent_docker_produces_no_fake_surfaces(self):
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(""),
            presence=lambda: False,
        )
        assert observation.domains == ()
        assert observation.evidence == ()

    def test_unknown_presence_is_truthful_unreachable(self):
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top(""),
            presence=lambda: "UNKNOWN",
        )
        assert observation.domains == ()
        unreachable = [e for e in observation.evidence if e.fact_type == "probe.unreachable"]
        assert unreachable and unreachable[0].value["reason_code"] == "DOCKER_PRESENCE_UNKNOWN"

    def test_daemon_list_failure_degrades_without_domains(self):
        def runner(cmd: list[str]) -> CommandResult:
            return _result("", 1)

        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=runner,
            presence=lambda: True,
        )
        assert observation.domains == ()
        unreachable = [e for e in observation.evidence if e.fact_type == "probe.unreachable"]
        assert unreachable and unreachable[0].value["reason_code"] == "DOCKER_LIST_UNAVAILABLE"

    def test_top_failure_keeps_domain_with_unknown_agents(self):
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=self._runner_with_top("PID ARGS\n", top_rc=1),
            presence=lambda: True,
        )
        assert len(observation.domains) == 1
        assert observation.agents == ()
        first = observation.domains[0]
        agent_capability = first.capabilities.get("agent_processes")
        assert agent_capability.status is CapabilityStatus.UNKNOWN
        unreachable = [
            e
            for e in observation.evidence
            if e.fact_type == "probe.unreachable"
            and e.value.get("scope") == "agents"
        ]
        assert unreachable, "per-container top failure must be recorded truthfully"


class TestSandboxWorkspaceBinding:
    TOP_WITH_AGENT = "PID ARGS\n31 /usr/local/bin/claude\n"

    def _observe(
        self,
        inspect: str,
        *,
        volume_created_at: str = "2026-07-28T13:33:50Z",
        volume_driver: str = "local",
        volume_returncode: int = 0,
    ) -> object:
        def runner(cmd: list[str]) -> CommandResult:
            if cmd[0:2] == ["docker", "ps"]:
                return _result(PS_OUTPUT)
            if cmd[0:2] == ["docker", "top"]:
                return _result(self.TOP_WITH_AGENT)
            if cmd[0:2] == ["docker", "inspect"]:
                return _result(inspect)
            if cmd[0:2] == ["docker", "info"]:
                return _result("engine-fixture")
            if cmd[0:3] == ["docker", "volume", "inspect"]:
                return _result(
                    f"agent-workspace\t{volume_driver}\tlocal\t"
                    f"{volume_created_at}\t/var/lib/docker/volumes/agent-workspace/_data",
                    volume_returncode,
                )
            raise AssertionError(f"unexpected command {cmd}")

        return observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=runner,
            presence=lambda: True,
        )

    def test_ambiguous_mount_ownership_fails_closed(self):
        observation = self._observe(INSPECT_AMBIGUOUS)
        assert observation.workspaces == ()
        for agent in observation.agents:
            assert agent.workspace_ids == ()
        assert not [e for e in observation.evidence if e.fact_type == "workspace.present"]

    def test_blocked_root_container_rootfs_never_binds(self):
        observation = self._observe(INSPECT_BLOCKED_ROOT)
        assert observation.workspaces == ()
        for agent in observation.agents:
            assert agent.workspace_ids == ()

    def test_internal_container_workspace_has_ephemeral_authority(self):
        inspect = "/srv/app\t2026-08-20T10:00:00Z\tsha256:helper-image\t\n"
        observation = self._observe(inspect)
        assert len(observation.workspaces) == 1
        assert len(observation.workspace_authorities) == 1
        authority = observation.workspace_authorities[0]
        assert authority.storage_kind == "CONTAINER_EPHEMERAL_FS"
        assert authority.durability == "EPHEMERAL"
        assert authority.protection_capability == "UNSUPPORTED"

    def test_tmpfs_workspace_authority_is_bound_but_not_protectable(self):
        inspect = (
            "/workspace\t2026-08-20T10:00:00Z\tsha256:helper-image\t"
            "tmpfs||/workspace|true;\n"
        )

        observation = self._observe(inspect)

        assert len(observation.workspace_authorities) == 1
        authority = observation.workspace_authorities[0]
        assert authority.storage_kind == "TMPFS"
        assert authority.durability == "VOLATILE"
        assert authority.protection_capability == "UNSUPPORTED"

    def test_container_restart_preserves_volume_workspace_but_refreshes_process(self):
        first = self._observe(INSPECT_MOUNTED)
        restarted = self._observe(
            INSPECT_MOUNTED.replace("2026-08-20T10:00:00Z", "2026-08-22T09:00:00Z")
        )
        assert len(first.workspaces) == 1 and len(restarted.workspaces) == 1
        assert first.workspaces[0].workspace_id == restarted.workspaces[0].workspace_id
        assert (
            first.workspace_authorities[0].process_instance_id
            != restarted.workspace_authorities[0].process_instance_id
        )

    def test_named_volume_authority_survives_container_restart(self):
        first_observation = self._observe(INSPECT_MOUNTED)
        restarted_observation = self._observe(
            INSPECT_MOUNTED.replace("2026-08-20T10:00:00Z", "2026-08-22T09:00:00Z")
        )

        def resolve(observation):
            report = ProductDiscoveryService(
                host_domain_observers=(lambda: observation,),
                runtime_adapter=_ResolverHostAdapter(),
                process_backend=_ResolverEmptyBackend(),
                clock=lambda: NOW,
            ).discover_with_authority()
            return resolve_host_workspace(
                report,
                home_path="/not-used-for-volume",
                system_roots=(),
            )

        first = resolve(first_observation)
        restarted = resolve(restarted_observation)
        assert first.status == restarted.status == "BOUND"
        assert first.workspace_id == restarted.workspace_id
        assert first.storage_resource_identity == restarted.storage_resource_identity
        assert first.process_instance_ids != restarted.process_instance_ids

    def test_recreated_same_name_volume_does_not_inherit_authority(self):
        first_observation = self._observe(INSPECT_MOUNTED)
        recreated_observation = self._observe(
            INSPECT_MOUNTED,
            volume_created_at="2026-08-29T12:00:00Z",
        )

        def resolve(observation):
            report = ProductDiscoveryService(
                host_domain_observers=(lambda: observation,),
                runtime_adapter=_ResolverHostAdapter(),
                process_backend=_ResolverEmptyBackend(),
                clock=lambda: NOW,
            ).discover_with_authority()
            return resolve_host_workspace(
                report,
                home_path="/not-used-for-volume",
                system_roots=(),
            )

        first = resolve(first_observation)
        recreated = resolve(recreated_observation)
        assert first.workspace_id != recreated.workspace_id
        assert first.storage_resource_identity != recreated.storage_resource_identity

    def test_bind_mount_maps_workdir_to_private_host_authority(self, tmp_path):
        root = tmp_path / "project"
        (root / ".git").mkdir(parents=True)
        (root / "sub").mkdir()
        observation = self._observe(_bind_inspect(root))

        assert len(observation.workspace_authorities) == 1
        private = observation.workspace_authorities[0]
        assert private.cwd == (root / "sub").resolve()
        assert private.agent_id == observation.agents[0].agent_id
        assert private.execution_domain_id == "host-native"
        assert str(root) not in json.dumps(
            [item.to_dict() for item in observation.evidence]
        )

        report = ProductDiscoveryService(
            host_domain_observers=(lambda: observation,),
            runtime_adapter=_ResolverHostAdapter(),
            process_backend=_ResolverEmptyBackend(),
            clock=lambda: NOW,
        ).discover_with_authority()
        result = resolve_host_workspace(
            report,
            home_path=tmp_path.parent,
            system_roots=(),
        )

        assert result.status == "BOUND"
        assert result.root_path == root.resolve()
        assert report.snapshot.agents[0].workspace_ids == (result.workspace_id,)
        assert report.snapshot.workspaces[0].workspace_id == result.workspace_id

    def test_named_volume_is_bound_authority_with_separate_backend_capability(self):
        observation = self._observe(INSPECT_MOUNTED)

        assert len(observation.workspaces) == 1
        assert len(observation.workspace_authorities) == 1
        authority = observation.workspace_authorities[0]
        assert authority.cwd is None
        assert authority.storage_kind == "DOCKER_NAMED_VOLUME"
        assert authority.logical_root == "/"
        assert authority.durability == "DURABLE"
        assert authority.current_reachability == "AVAILABLE"
        assert authority.protection_capability == "SUPPORTED"
        assert authority.storage_resource_identity.startswith("sha256:")
        workspace_evidence = next(
            item for item in observation.evidence if item.fact_type == "workspace.present"
        )
        assert workspace_evidence.value["authority_reason_code"] == (
            "WORKSPACE_AUTHORITY_CANDIDATE"
        )
        assert workspace_evidence.value["storage_kind"] == "DOCKER_NAMED_VOLUME"

    def test_named_volume_without_helper_runtime_keeps_authority(self):
        inspect = (
            "/workspace\t2026-08-20T10:00:00Z\t"
            "volume|agent-workspace|/workspace|true;\n"
        )

        observation = self._observe(inspect)

        assert len(observation.workspace_authorities) == 1
        authority = observation.workspace_authorities[0]
        assert authority.storage_kind == "DOCKER_NAMED_VOLUME"
        assert authority.protection_capability == "UNSUPPORTED"
        assert authority.protection_reason_code == (
            "DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE"
        )

    def test_unsupported_volume_driver_keeps_authority_but_blocks_backend(self):
        observation = self._observe(INSPECT_MOUNTED, volume_driver="nfs")

        assert len(observation.workspace_authorities) == 1
        authority = observation.workspace_authorities[0]
        assert authority.storage_kind == "DOCKER_NAMED_VOLUME"
        assert authority.protection_capability == "UNSUPPORTED"

    def test_readonly_bind_mount_remains_valid_workspace_authority(self, tmp_path):
        root = tmp_path / "project"
        (root / "sub").mkdir(parents=True)

        observation = self._observe(_bind_inspect(root, writable=False))

        assert len(observation.workspaces) == 1
        assert len(observation.workspace_authorities) == 1
        authority = observation.workspace_authorities[0]
        assert authority.cwd == (root / "sub").resolve()
        assert authority.storage_kind == "DOCKER_BIND"
        assert authority.agent_mutation_capability == "READ_ONLY"

    def test_bind_mapping_rejects_traversal_and_prefix_confusion(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        traversal = (
            f"/workspace/project/../escape\t2026-08-20T10:00:00Z\t"
            f"bind|{root}|/workspace/project|true;\n"
        )
        prefix = (
            f"/workspace/project-other\t2026-08-20T10:00:00Z\t"
            f"bind|{root}|/workspace/project|true;\n"
        )

        assert self._observe(traversal).workspace_authorities == ()
        assert self._observe(prefix).workspace_authorities == ()

    def test_container_runtime_restart_keeps_durable_workspace_identity(self, tmp_path):
        root = tmp_path / "project"
        (root / ".git").mkdir(parents=True)
        (root / "sub").mkdir()
        first_observation = self._observe(_bind_inspect(root))
        next_observation = self._observe(
            _bind_inspect(root, started_at="2026-08-22T09:00:00Z")
        )

        first = ProductDiscoveryService(
            host_domain_observers=(lambda: first_observation,),
            runtime_adapter=_ResolverHostAdapter(),
            process_backend=_ResolverEmptyBackend(),
            clock=lambda: NOW,
        ).discover_with_authority()
        restarted = ProductDiscoveryService(
            host_domain_observers=(lambda: next_observation,),
            runtime_adapter=_ResolverHostAdapter(),
            process_backend=_ResolverEmptyBackend(),
            clock=lambda: NOW,
        ).discover_with_authority()

        first_result = resolve_host_workspace(
            first, home_path=tmp_path.parent, system_roots=()
        )
        restarted_result = resolve_host_workspace(
            restarted, home_path=tmp_path.parent, system_roots=()
        )
        assert first.workspace_authorities[0].process_instance_id != (
            restarted.workspace_authorities[0].process_instance_id
        )
        assert first_result.workspace_id == restarted_result.workspace_id
        assert first_result.root_digest == restarted_result.root_digest

    def test_bind_authority_projects_identically_for_agents_and_supervision(
        self,
        tmp_path,
    ):
        from agentguard.api.r4_projection import R4ReadProjectionService
        from agentguard.evidence.discovery_adapter import record_discovery_snapshot
        from agentguard.recovery.workspace_scope import WorkspaceScopeService
        from agentguard.storage.db import StateDB
        from agentguard.storage.snapshots import SnapshotStore

        root = tmp_path / "project"
        (root / ".git").mkdir(parents=True)
        (root / "sub").mkdir()
        observation = self._observe(_bind_inspect(root))
        report = ProductDiscoveryService(
            host_domain_observers=(lambda: observation,),
            runtime_adapter=_ResolverHostAdapter(),
            process_backend=_ResolverEmptyBackend(),
            clock=lambda: NOW,
        ).discover_with_authority()
        resolved = resolve_host_workspace(
            report,
            home_path=tmp_path.parent,
            system_roots=(),
        )
        database = StateDB(tmp_path / "state.db")
        database.connect()
        try:
            record_discovery_snapshot(database, report.snapshot, recorded_at=NOW)
            WorkspaceScopeService(database).bind(
                resolved,
                recorded_at=NOW,
                discovery_snapshot_id=report.snapshot.snapshot_id,
            )
            projection = R4ReadProjectionService(
                database,
                SnapshotStore(tmp_path / "snapshots"),
            )
            agent = projection.agents()["items"][0]
            supervised = projection.supervision()["observed_agents"][0]
            recovery = projection.recovery()
        finally:
            database.close()

        assert agent["workspace_correlation"]["status"] == "LINKED"
        assert agent["workspace"]["authority_state"] == "BOUND"
        assert agent["workspace"]["workspace_id"] == resolved.workspace_id
        assert supervised["workspace_correlation"] == agent["workspace_correlation"]
        assert supervised["workspace"] == agent["workspace"]
        assert recovery["action_eligible"] is True
        assert recovery["eligibility_reason_code"] is None
        assert recovery["checkpoint_count"] == 0

    def test_ambiguous_writable_bind_mounts_fail_closed(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        inspect = (
            "/workspace\t2026-08-20T10:00:00Z\t"
            f"bind|{first}|/workspace|true;"
            f"bind|{second}|/workspace|true;\n"
        )

        observation = self._observe(inspect)

        assert observation.workspaces == ()
        assert observation.workspace_authorities == ()
        candidate = next(
            item for item in observation.evidence if item.fact_type == "workspace.candidate"
        )
        assert candidate.value["reason_code"] == "WORKSPACE_EVIDENCE_AMBIGUOUS"

    def test_drive_or_filesystem_root_bind_fails_generic_authority(self):
        inspect = (
            "/workspace\t2026-08-20T10:00:00Z\t"
            "bind|/|/workspace|true;\n"
        )
        observation = self._observe(inspect)
        report = ProductDiscoveryService(
            host_domain_observers=(lambda: observation,),
            runtime_adapter=_ResolverHostAdapter(),
            process_backend=_ResolverEmptyBackend(),
            clock=lambda: NOW,
        ).discover_with_authority()

        resolved = resolve_host_workspace(report, home_path="/home/agent", system_roots=())

        assert resolved.status == "UNAVAILABLE"
        assert resolved.reason_code == "WORKSPACE_SCOPE_TOO_BROAD"

    def test_binding_flows_to_workspace_linked_and_resolver(self, tmp_path):
        """The existing adapter materializes WORKSPACE_LINKED from the
        snapshot, and the correlation resolver verifies it from the Ledger.
        This is not durable workspace protection authority."""
        from agentguard.evidence.discovery_adapter import (
            discovery_events,
            record_discovery_snapshot,
            resolve_verified_workspace_correlation,
        )
        from agentguard.storage.db import StateDB

        observation = self._observe(INSPECT_MOUNTED)

        from agentguard.discovery.product import ProductDiscoveryService

        service = ProductDiscoveryService(
            host_domain_observers=(lambda: observation,),
            runtime_adapter=_ResolverHostAdapter(),
            process_backend=_ResolverEmptyBackend(),
            clock=lambda: NOW,
        )
        report = service.discover_with_authority()
        events = discovery_events(report.snapshot, recorded_at=NOW)
        linked = [e for e in events if e.event_type.value == "WORKSPACE_LINKED"]
        assert len(linked) == 1, "adapter must derive exactly one sandbox binding"

        db = StateDB(tmp_path / "binding.db")
        db.connect()
        try:
            record_discovery_snapshot(db, report.snapshot, recorded_at=NOW)
            assert db._conn is not None
            domain_id = observation.domains[0].domain_id
            binding = resolve_verified_workspace_correlation(
                db._conn, execution_domain_id=domain_id
            )
            assert binding["status"] == "LINKED", binding
            assert binding["reason_code"] == "WORKSPACE_CORRELATION_VERIFIED"
            assert binding["workspace_id"] == observation.workspaces[0].workspace_id
            assert binding["binding_ref"]
        finally:
            db.close()


class _ResolverHostAdapter:
    def discover(self):
        from agentguard.discovery import (
            CapabilityAssessment,
            CapabilityStatus,
            DiscoverySnapshot,
            DomainCapabilities,
            ExecutionDomainDescriptor,
            ExecutionDomainKind,
            ProbeEvidence,
        )

        return DiscoverySnapshot(
            snapshot_id="host",
            observed_at=NOW,
            domains=(
                ExecutionDomainDescriptor(
                    domain_id="windows-current",
                    kind=ExecutionDomainKind.WINDOWS,
                    capabilities=DomainCapabilities(
                        {
                            "self_visible": CapabilityAssessment(
                                status=CapabilityStatus.AVAILABLE,
                                reason_code="CURRENT",
                                evidence_ids=("host-de",),
                                confidence=0.9,
                            )
                        }
                    ),
                    evidence_ids=("host-de",),
                    confidence=0.9,
                ),
            ),
            evidence=(
                ProbeEvidence(
                    evidence_id="host-de",
                    collector="self",
                    source="local",
                    observed_at=NOW,
                    fact_type="platform.current",
                    value={},
                    status=CapabilityStatus.AVAILABLE,
                    sanitized=True,
                ),
            ),
            status=CapabilityStatus.AVAILABLE,
        )


class _ResolverEmptyBackend:
    def iter_processes(self):
        return ()


class TestWslDomainObservation:
    def test_running_distro_is_domain_with_honest_unknown_agents(self):
        wsl_output = (
            "  NAME      STATE      VERSION\n"
            " * Ubuntu    Running    2\n"
            "   Debian    Stopped    2\n"
        )
        observation = observe_wsl_domains(
            clock=lambda: NOW,
            runner=lambda cmd: _result(wsl_output),
            windows=True,
        )
        assert [d.kind for d in observation.domains] == [ExecutionDomainKind.WSL]
        domain = observation.domains[0]
        capability = domain.capabilities.get("agent_processes")
        assert capability.status is CapabilityStatus.UNKNOWN
        assert capability.reason_code == "NO_BOUNDED_HOST_READ"
        assert observation.agents == ()
        assert [r.runtime_type for r in observation.runtimes] == ["WSL_DISTRO_RUNTIME"]
        unreachable = [
            item
            for item in observation.evidence
            if item.fact_type == "probe.unreachable"
        ]
        assert len(unreachable) == 1
        assert unreachable[0].value == {
            "domain_label": "Ubuntu",
            "execution_domain_id": domain.domain_id,
            "reason_code": "NO_BOUNDED_HOST_READ",
            "scope": "agents",
        }

    def test_non_windows_and_absent_wsl_are_empty(self):
        empty = observe_wsl_domains(
            clock=lambda: NOW, runner=lambda cmd: _result(""), windows=False
        )
        assert empty.domains == () and empty.evidence == ()
        absent = observe_wsl_domains(
            clock=lambda: NOW,
            runner=lambda cmd: _result("", 1),
            windows=True,
        )
        assert absent.domains == () and absent.evidence == ()


class _RuntimeAdapter:
    def discover(self) -> DiscoverySnapshot:
        raise AssertionError("replaced below")


class TestProductDiscoveryIntegration:
    def _service(self, observers) -> ProductDiscoveryService:
        class _Adapter:
            def discover(self) -> DiscoverySnapshot:
                from agentguard.discovery import (
                    CapabilityAssessment,
                    DomainCapabilities,
                    ExecutionDomainDescriptor,
                    ProbeEvidence,
                )

                return DiscoverySnapshot(
                    snapshot_id="self",
                    observed_at=NOW,
                    domains=(
                        ExecutionDomainDescriptor(
                            domain_id="windows-current",
                            kind=ExecutionDomainKind.WINDOWS,
                            capabilities=DomainCapabilities(
                                {
                                    "self_visible": CapabilityAssessment(
                                        status=CapabilityStatus.AVAILABLE,
                                        reason_code="CURRENT",
                                        evidence_ids=("de",),
                                        confidence=0.9,
                                    )
                                }
                            ),
                            evidence_ids=("de",),
                            confidence=0.9,
                        ),
                    ),
                    evidence=(
                        ProbeEvidence(
                            evidence_id="de",
                            collector="self",
                            source="local",
                            observed_at=NOW,
                            fact_type="platform.current",
                            value={},
                            status=CapabilityStatus.AVAILABLE,
                            sanitized=True,
                        ),
                    ),
                    status=CapabilityStatus.AVAILABLE,
                )

        class _Backend:
            def iter_processes(self):
                return ()

        return ProductDiscoveryService(
            runtime_adapter=_Adapter(),
            process_backend=_Backend(),
            clock=lambda: NOW,
            host_domain_observers=observers,
        )

    def test_observer_results_join_the_snapshot(self):
        observation = HostDomainObservation()
        # Reuse the real docker observer with a scripted runner for the join.
        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=lambda cmd: _result(
                "abc123def456\trunner\tUp 2 hours\n"
            )
            if cmd[0:2] == ["docker", "ps"]
            else _result("PID ARGS\n31 node /x/node_modules/@openai/codex/bin/codex.js\n"),
            presence=lambda: True,
        )
        snapshot = self._service((lambda: observation,)).discover()

        assert [d.kind for d in snapshot.domains] == [
            ExecutionDomainKind.WINDOWS,
            ExecutionDomainKind.CONTAINER,
        ]
        assert [a.agent_type for a in snapshot.agents] == ["CODEX"]
        facts = {e.fact_type for e in snapshot.evidence}
        assert {"runtime.metadata", "agent.metadata"} <= facts

    def test_observer_failure_never_erases_host_discovery(self):
        def exploding():
            raise RuntimeError("docker CLI exploded")

        snapshot = self._service((exploding,)).discover()

        assert snapshot.domains[0].kind is ExecutionDomainKind.WINDOWS
        failure = [
            e
            for e in snapshot.evidence
            if e.fact_type == "probe.unreachable"
            and e.value.get("reason_code") == "HOST_DOMAIN_OBSERVER_FAILED"
        ]
        assert failure, "observer crash must surface as truthful unreachable evidence"
