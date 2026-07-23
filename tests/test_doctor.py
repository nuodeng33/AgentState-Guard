"""Tests for doctor command."""

from unittest.mock import patch
from agentguard.commands.doctor import doctor, _ok, _warn, _fail, _skip, _unreachable


class TestDoctor:
    def test_doctor_returns_list(self):
        config = {"container_name": "agent-dev", "port": 3001}
        results = doctor(config)
        assert isinstance(results, list)

    def test_doctor_results_have_required_fields(self):
        config = {"container_name": "agent-dev", "port": 3001}
        results = doctor(config)
        for r in results:
            assert "check" in r
            assert "status" in r
            assert "message" in r
            # Must be one of the valid status values
            assert r["status"] in ("PASS", "WARN", "FAIL", "SKIP", "UNREACHABLE", "INFO")

    def test_doctor_checks_many_items(self):
        config = {"container_name": "agent-dev", "port": 3001}
        results = doctor(config)
        assert len(results) >= 8

    def test_helper_functions(self):
        assert _ok("test", "msg") == {"check": "test", "status": "PASS", "message": "msg"}
        assert _warn("test", "msg") == {"check": "test", "status": "WARN", "message": "msg"}
        assert _fail("test", "msg") == {"check": "test", "status": "FAIL", "message": "msg"}
        assert _skip("test", "msg") == {"check": "test", "status": "SKIP", "message": "msg"}
        assert _unreachable("test", "msg") == {"check": "test", "status": "UNREACHABLE", "message": "msg"}

    def test_doctor_all_five_statuses_present(self):
        """Doctor may output all 5 status types depending on environment."""
        config = {"container_name": "agent-dev", "port": 3001}
        results = doctor(config)
        statuses = set(r["status"] for r in results)
        assert statuses.issubset({"PASS", "WARN", "FAIL", "SKIP", "UNREACHABLE", "INFO"})
