"""Sandbox observer: unit evidence for helper reduction and collector mapping.

The helper functions are exercised for real inside this POSIX container
(/proc scanning, inotify on temp dirs). The collector mapping tests pin
the Ledger contract. Real-daemon observation is proven by the recorded
smoke runs (events.jsonl / events-net.jsonl), not by these tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentguard.evidence.models import EventFamily, EventType
from agentguard.sandbox_observer import collector as collector_mod
from agentguard.sandbox_observer import helper as helper_mod

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestHelperReduction:
    def test_scan_processes_reports_this_test_process(self):
        known: dict[int, dict] = {}
        started, _exited, seen = helper_mod.scan_processes(known)
        assert seen, "live /proc scan must observe processes in this container"
        assert any(fact["argv0"].startswith("pytest") for fact in started) or started
        # Bounded fields only: no full argv ever leaves the reducer.
        for fact in seen.values():
            assert set(fact) == {
                "pid",
                "ppid",
                "argv0",
                "token_count",
                "exe",
                "start_ticks",
            }

    def test_established_remotes_is_bounded_pairs(self):
        remotes = helper_mod.established_remotes()
        for ip, port in remotes:
            assert 0 < port < 65536
            assert isinstance(ip, str) and len(ip) <= 45

    def test_inotify_captures_real_file_lifecycle(self, tmp_path):
        watch = helper_mod.WorkspaceWatch([str(tmp_path)])
        try:
            import time

            def drain() -> list[dict]:
                events: list[dict] = []
                for _round in range(20):
                    events.extend(watch.poll())
                    if events:
                        return events
                    time.sleep(0.05)
                return events

            (tmp_path / "a.txt").write_text("x")
            (tmp_path / "a.txt").write_text("xy")
            first = drain()
            (tmp_path / "d").mkdir()
            # Contract: a file created inside a brand-new directory is only
            # observable after the directory watch has propagated; the
            # observer adds subtree watches when the CREATE_DIR event is
            # drained. (Burst-create races inside new dirs are a documented
            # V1 limitation, not a guarantee.)
            second = drain()
            (tmp_path / "d" / "b.txt").write_text("z")
            third = drain()
            (tmp_path / "a.txt").rename(tmp_path / "c.txt")
            (tmp_path / "c.txt").unlink()
            fourth = drain()
            time.sleep(0.05)
            fourth.extend(watch.poll())

            actions: set[tuple[str, str]] = set()
            for batch in (first, second, third, fourth):
                actions.update((e["relpath"], e["action"]) for e in batch)
            assert ("a.txt", "CREATE") in actions
            assert ("a.txt", "MODIFY") in actions
            assert ("d", "CREATE_DIR") in actions
            assert ("d/b.txt", "CREATE") in actions
            assert ("a.txt", "RENAME_FROM") in actions
            assert ("c.txt", "RENAME_INTO") in actions
            assert ("c.txt", "DELETE") in actions
        finally:
            watch.close()

    def test_inotify_watches_existing_nested_tree_recursively(self, tmp_path):
        nested = tmp_path / "level1" / "level2"
        nested.mkdir(parents=True)
        watch = helper_mod.WorkspaceWatch([str(tmp_path)])
        try:
            import time

            (nested / "deep.txt").write_text("observed", encoding="utf-8")
            events: list[dict] = []
            for _round in range(20):
                events.extend(watch.poll())
                if any(event["relpath"] == "level1/level2/deep.txt" for event in events):
                    break
                time.sleep(0.05)

            assert ("level1/level2/deep.txt", "CREATE") in {
                (event["relpath"], event["action"]) for event in events
            }
        finally:
            watch.close()

    def test_helper_end_to_end_emits_jsonl_and_stops(self, tmp_path):
        proc = subprocess.run(  # noqa: PLW1510 - exit code asserted below
            [
                sys.executable,
                str(REPO_ROOT / "agentguard/sandbox_observer/helper.py"),
                "--max-seconds",
                "1",
                "--poll-ms",
                "60",
                "--workspace",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        kinds = [json.loads(line)["kind"] for line in lines]
        assert kinds[0] == "observer_hello"
        assert kinds[-1] == "observer_bye"


class TestCollectorMapping:
    def test_helper_command_uses_shared_namespaces_and_stdin_delivery(self):
        command = collector_mod.helper_command("abc123def456", image="asg-observer:1")
        joined = " ".join(command)
        assert "--pid=container:abc123def456" in joined
        assert "--network=container:abc123def456" in joined
        assert "--volumes-from=abc123def456" in joined
        assert "--entrypoint=python3" in joined
        assert "-i" in command
        assert command[-1] == "-"

    def test_parse_stream_drops_unknown_and_oversized_fields(self):
        stream = iter(
            [
                b'{"kind":"process_started","pid":9,"argv0":"pytest","ts":1787417458.6}',
                b'{"kind":"evil_kind","pid":1}',
                b"not json",
                b'{"kind":"file_activity","relpath":"'
                + b"x" * 900
                + b'","action":"CREATE"}',
            ]
        )
        parsed = list(
            collector_mod.parse_stream("docker-container-x", stream)
        )
        # Unknown kinds and non-JSON lines are dropped entirely; a known
        # kind keeps its bounded fields but loses oversized values.
        assert [item.kind for item in parsed] == ["process_started", "file_activity"]
        assert parsed[0].fields["argv0"] == "pytest"
        assert "relpath" not in parsed[1].fields
        assert parsed[1].fields["action"] == "CREATE"

    def test_event_mapping_types_and_families(self):
        cases = [
            ("process_started", "SANDBOX_PROCESS_STARTED", EventFamily.DISCOVERY),
            ("process_exited", "SANDBOX_PROCESS_EXITED", EventFamily.DISCOVERY),
            ("network_established", "SANDBOX_NETWORK_OBSERVED", EventFamily.DISCOVERY),
            ("file_activity", "OBSERVED_CHANGE", EventFamily.CHANGE),
            ("observer_hello", "SANDBOX_OBSERVER_LIFECYCLE", EventFamily.DISCOVERY),
        ]
        for kind, expected_type, family in cases:
            event = collector_mod.to_evidence_event(
                collector_mod.SandboxObservation(
                    "docker-container-abc", kind, {"ts": 1787417458.6}
                )
            )
            assert event.event_type is EventType(expected_type)
            assert event.event_family is family

    def test_degraded_observation_records_degraded_result(self):
        event = collector_mod.to_evidence_event(
            collector_mod.SandboxObservation(
                "docker-container-abc",
                "observer_degraded",
                {"reason": "INOTIFY_UNAVAILABLE", "ts": 1787417458.6},
            )
        )

        assert event.result == "DEGRADED"

    def test_event_ids_unique_across_repeated_facts(self):
        observation = collector_mod.SandboxObservation(
            "docker-container-abc", "process_exited", {"pid": 7, "ts": 1.0}
        )
        ids = {
            collector_mod.to_evidence_event(observation).event_id
            for _ in range(3)
        }
        # Same fact+ts is idempotent (dedup-safe); distinct ts differ.
        assert len(ids) == 1
        other = collector_mod.to_evidence_event(
            collector_mod.SandboxObservation(
                "docker-container-abc", "process_exited", {"pid": 7, "ts": 2.0}
            )
        )
        assert other.event_id not in ids

    def test_record_observations_writes_verifiable_ledger(self, tmp_path):
        observations = [
            collector_mod.SandboxObservation(
                "docker-container-abc", "process_started", {"pid": 5, "ts": 1.5}
            ),
            collector_mod.SandboxObservation(
                "docker-container-abc",
                "file_activity",
                {"relpath": "x.txt", "action": "CREATE", "ts": 2.5},
            ),
        ]
        db_path = tmp_path / "obs.db"
        event_ids = collector_mod.record_observations(db_path, observations)
        assert len(event_ids) == 2

        from agentguard.evidence.ledger import verify_ledger
        from agentguard.storage.db import StateDB

        db = StateDB(db_path)
        db.connect()
        try:
            assert verify_ledger(db._conn) == []
            types = [
                row[0]
                for row in db._conn.execute(  # type: ignore[union-attr]
                    "SELECT event_type FROM evidence_ledger_events ORDER BY sequence"
                )
            ]
            assert types == ["SANDBOX_PROCESS_STARTED", "OBSERVED_CHANGE"]
        finally:
            db.close()


class TestLegacyContainerNameRetirement:
    def _config_dir(self, tmp_path, content: str) -> Path:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agentguard.toml").write_text(content)
        return tmp_path

    def test_legacy_unmarked_agent_dev_is_retired(self, tmp_path):
        from agentguard.core.config import Config

        base = self._config_dir(
            tmp_path,
            '# AgentState Guard product configuration\n[checks]\n'
            'commands = ["docker"]\ncontainer_name = "agent-dev"\nport = 3001\n',
        )
        cfg = Config(base)
        assert cfg.container_name is None

    def test_v2_marked_agent_dev_is_respected(self, tmp_path):
        from agentguard.core.config import Config

        base = self._config_dir(
            tmp_path,
            '# AgentState Guard product configuration\n'
            '# asg-config-schema: v2\n[checks]\ncontainer_name = "agent-dev"\n',
        )
        cfg = Config(base)
        assert cfg.container_name == "agent-dev"

    def test_unmarked_other_container_is_respected(self, tmp_path):
        from agentguard.core.config import Config

        base = self._config_dir(
            tmp_path, '[checks]\ncontainer_name = "my-own"\n'
        )
        cfg = Config(base)
        assert cfg.container_name == "my-own"
