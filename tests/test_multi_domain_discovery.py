"""Multi-domain discovery: Docker/WSL observed read-only from the host.

Positive: same supported known identities inside observable execution
domains (docker top rows). Negative: generic runtimes, unrelated commands
carrying agent-looking arguments, and unclassified executables (zcode)
stay out. Domain failure: docker daemon unreachable degrades truthfully
and never erases host-native discovery.
"""

from __future__ import annotations

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

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _result(stdout: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(returncode, stdout, "")


PS_OUTPUT = "abc123def456\tmy-runner\tUp 3 hours\n"

#: Real-shaped docker inspect bounded line: WorkingDir \t StartedAt \t mounts.
INSPECT_MOUNTED = (
    "/workspace\t2026-08-20T10:00:00Z\t"
    "volume:agent-workspace=>/workspace;"
    "bind:/var/run/sock=>/var/run/sock;\n"
)
INSPECT_AMBIGUOUS = (
    "/data/sub\t2026-08-20T10:00:00Z\t"
    "volume:one=>/data;volume:two=>/data/sub;\n"
)
INSPECT_BLOCKED_ROOT = (
    "\t2026-08-20T10:00:00Z\t"
    "overlay:x=>/;volume:agent-workspace=>/workspace;\n"
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

    def test_real_dogfood_launchers_are_admitted_without_generic_widening(self):
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
            "CODEX",
            "KIMI_CODE",
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
            raise AssertionError(f"unexpected command {cmd}")

        observation = observe_docker_domains(
            admit_process=_admit_container_process,
            clock=lambda: NOW,
            runner=runner,
            presence=lambda: True,
            docker_executable=resolved,
        )

        assert [command[1] for command in calls] == ["ps", "top", "inspect"]
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

    def _observe(self, inspect: str) -> object:
        def runner(cmd: list[str]) -> CommandResult:
            if cmd[0:2] == ["docker", "ps"]:
                return _result(PS_OUTPUT)
            if cmd[0:2] == ["docker", "top"]:
                return _result(self.TOP_WITH_AGENT)
            if cmd[0:2] == ["docker", "inspect"]:
                return _result(inspect)
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

    def test_unowned_workdir_fails_closed(self):
        inspect = "/srv/app\t2026-08-20T10:00:00Z\tvolume:ws=>/workspace;\n"
        observation = self._observe(inspect)
        assert observation.workspaces == ()

    def test_container_restart_changes_workspace_identity(self):
        first = self._observe(INSPECT_MOUNTED)
        restarted = self._observe(
            INSPECT_MOUNTED.replace("2026-08-20T10:00:00Z", "2026-08-22T09:00:00Z")
        )
        assert len(first.workspaces) == 1 and len(restarted.workspaces) == 1
        assert first.workspaces[0].workspace_id != restarted.workspaces[0].workspace_id

    def test_binding_flows_to_workspace_linked_and_resolver(self, tmp_path):
        """The existing adapter materializes WORKSPACE_LINKED from the
        snapshot, and the existing supervision resolver verifies it from
        the ledger — no second binding abstraction anywhere."""
        from agentguard.evidence.discovery_adapter import (
            discovery_events,
            record_discovery_snapshot,
            resolve_verified_workspace_binding,
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
            binding = resolve_verified_workspace_binding(
                db._conn, execution_domain_id=domain_id
            )
            assert binding["status"] == "BOUND", binding
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
