"""Tests for report command."""

from pathlib import Path
from datetime import datetime, timezone
from agentguard.commands.report import _render_markdown, _build_report_data


class TestReport:
    def test_render_markdown_no_checkpoints(self):
        data = {
            "generated_at": "2026-01-01T00:00:00",
            "tool": "AgentState Guard v0.1.0",
            "environment": {"docker_available": False, "workspace": "/tmp"},
            "checkpoints": [],
            "summary": {"total_checkpoints": 0, "latest_id": None, "latest_label": None},
        }
        md = _render_markdown(data)
        assert "# AgentState Guard Report" in md
        assert "No checkpoints recorded" in md

    def test_render_markdown_with_checkpoints(self):
        data = {
            "generated_at": "2026-01-01T00:00:00",
            "tool": "AgentState Guard v0.1.0",
            "environment": {"docker_available": True, "workspace": "/tmp"},
            "checkpoints": [
                {"id": 1, "label": "initial", "created_at": "2026-01-01T00:00:00",
                 "file_count": 5, "git_branch": "main", "git_commit": "abc123"},
            ],
            "summary": {"total_checkpoints": 1, "latest_id": 1, "latest_label": "initial"},
        }
        md = _render_markdown(data)
        assert "initial" in md
        assert "✅" in md

    def test_render_markdown_docker_not_available(self):
        data = {
            "generated_at": "2026-01-01T00:00:00",
            "tool": "AgentState Guard v0.1.0",
            "environment": {"docker_available": False, "workspace": "/tmp"},
            "checkpoints": [],
            "summary": {"total_checkpoints": 0, "latest_id": None, "latest_label": None},
        }
        md = _render_markdown(data)
        assert "❌" in md

    def test_build_report_data_structure(self):
        checkpoints = [
            {"id": 1, "label": "cp1", "created_at": "2026-01-01T00:00:00",
             "file_count": 3, "git_branch": "main", "git_commit": "abc"}
        ]
        data = _build_report_data(checkpoints, {})
        assert data["summary"]["total_checkpoints"] == 1
        assert data["summary"]["latest_id"] == 1
        assert data["tool"] == "AgentState Guard v0.1.0"

    def test_build_report_data_empty(self):
        data = _build_report_data([], {})
        assert data["summary"]["total_checkpoints"] == 0
        assert data["summary"]["latest_id"] is None
