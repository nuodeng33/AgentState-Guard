"""Environment truth surface: no historical dev defaults become product facts.

Regression for the physical dogfood failure where the packaged product
reported "agent-dev is running" purely because a development-era default
container name leaked through config plumbing. Docker capability must be
independent of any fixed container name, and a named container may only
appear when this installation explicitly configures one.
"""

from unittest.mock import patch

from agentguard.commands import status as status_mod
from agentguard.commands.doctor import doctor
from agentguard.core.config import DEFAULT_CONFIG, PRODUCT_CONFIG_TEMPLATE
from agentguard.storage.db import StateDB


class TestNoImplicitHistoricalContainer:
    def test_status_reports_no_named_container_without_explicit_config(self):
        calls: list[str] = []

        def spy_container_running(name):
            calls.append(name)
            return True, "abc123 Up 2 hours"

        with (
            patch.object(status_mod, "docker_presence", return_value=True),
            patch.object(status_mod, "container_running", side_effect=spy_container_running),
            patch.object(status_mod, "all_versions", return_value={}),
        ):
            result = status_mod.status({})

        # Docker capability is reported; the named-container fact is not,
        # even though a container with the historical dev name exists.
        assert result["checks"]["docker"] is True
        assert "container_running" not in result["checks"]
        assert "container_info" not in result["checks"]
        assert calls == []

    def test_status_reports_explicitly_configured_container_for_compat(self):
        with (
            patch.object(status_mod, "docker_presence", return_value=True),
            patch.object(status_mod, "container_running", return_value=(True, "id1 Up")),
            patch.object(status_mod, "all_versions", return_value={}),
        ):
            flat = status_mod.status({"container_name": "my-own-container"})
            nested = status_mod.status({"checks": {"container_name": "my-own-container"}})

        for result in (flat, nested):
            assert result["checks"]["container_running"] is True
            assert result["checks"]["container_info"] == "id1 Up"

    def test_docker_capability_independent_from_container_name(self):
        """Docker absence is still truthful without any container probe."""

        with (
            patch.object(status_mod, "docker_presence", return_value=False),
            patch.object(status_mod, "all_versions", return_value={}),
        ):
            result = status_mod.status({"container_name": "anything"})

        assert result["checks"]["docker"] is False
        assert "container_running" not in result["checks"]

    def test_doctor_never_claims_historical_default_container(self):
        from agentguard.core.runner import CommandResult

        def ok_run(cmd, **kwargs):
            return CommandResult(0, "24.0.7", "")

        with (
            patch("agentguard.commands.doctor.which", return_value="/usr/bin/docker"),
            patch("agentguard.commands.doctor.run_command", side_effect=ok_run),
        ):
            rows = doctor({})

        # No container-scoped row may assert anything about a named container
        # when none is configured. (The self-environment INFO row and the
        # POSIX port row are different, legitimate facts.)
        for row in rows:
            if row.get("check") != "container":
                continue
            message = str(row.get("message", ""))
            assert "agent-dev" not in message
            assert "is running" not in message
            assert "not running" not in message
        daemon_rows = [r for r in rows if r.get("check") == "docker-daemon"]
        assert daemon_rows and "Docker daemon" in daemon_rows[0]["message"]

    def test_defaults_and_template_carry_no_historical_container_name(self):
        assert DEFAULT_CONFIG["checks"]["container_name"] is None
        assert "agent-dev" not in PRODUCT_CONFIG_TEMPLATE

    def test_checkpoint_records_empty_container_facts_without_config(self, tmp_path):
        from agentguard.commands.checkpoint import cmd_checkpoint
        from agentguard.core.snapshot import create_snapshot as real_create_snapshot
        from agentguard.storage.snapshots import SnapshotStore

        captured: dict = {}

        def spy(**kwargs):
            captured.update(kwargs)
            return real_create_snapshot(**kwargs)

        db = StateDB(tmp_path / "state.db")
        db.connect()
        store = SnapshotStore(tmp_path / "snaps")
        try:
            with (
                patch(
                    "agentguard.commands.checkpoint.create_snapshot", side_effect=spy
                ),
                patch(
                    "agentguard.commands.checkpoint._git_info",
                    return_value=("main", "x" * 40),
                ),
                patch(
                    "agentguard.commands.checkpoint.all_versions", return_value={}
                ),
                patch("agentguard.commands.checkpoint.container_info") as info,
            ):
                cmd_checkpoint(label="truth", config={}, db=db, snapshots=store)
            info.assert_not_called()
            assert captured["container_state"] == {}
        finally:
            db.close()
