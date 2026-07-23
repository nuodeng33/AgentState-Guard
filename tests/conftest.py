"""Shared test fixtures."""

import tempfile
from pathlib import Path
from typing import Generator, Tuple

import pytest


@pytest.fixture
def tmp_project() -> Generator[Path, None, None]:
    """Create a temporary project directory for testing."""
    with tempfile.TemporaryDirectory(prefix="agentguard-test-") as td:
        yield Path(td)


@pytest.fixture
def config_dir(tmp_project: Path) -> Path:
    """Create config directory with sample agentguard.yaml."""
    config_path = tmp_project / "config"
    config_path.mkdir(parents=True)
    yaml_content = """
checks:
  container_name: test-container
  port: 9999
security:
  restore_whitelist:
    - /tmp/agentguard-test/*
  ignore_directories:
    - __pycache__
paths:
  state_dir: .agentguard
  snapshot_dir: .agentguard/snapshots
  docs_dir: docs
  reports_dir: reports
"""
    (config_path / "agentguard.yaml").write_text(yaml_content)
    return config_path


@pytest.fixture
def test_file(tmp_project: Path) -> Path:
    """Create a test file with known content."""
    f = tmp_project / "test.txt"
    f.write_text("Hello, AgentState Guard!")
    return f


@pytest.fixture
def secret_file(tmp_project: Path) -> Path:
    """Create a file containing simulated secrets."""
    f = tmp_project / "settings.json"
    f.write_text(
        '{\n'
        '  "api_key": "sk-ant-test1234567890abcdef",\n'
        '  "token": "ghp_testtoken1234567890",\n'
        '  "password": "supersecret123",\n'
        '  "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.test",\n'
        '  "safe_field": "hello"\n'
        '}'
    )
    return f
