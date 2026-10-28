"""Shared test fixtures."""

import tempfile
import threading
from collections.abc import Generator
from pathlib import Path

import pytest


def pytest_configure(config):
    # Workspace tests deliberately resolve the nearest Git root. A temp tree
    # inside any checkout changes their inputs and can scan the checkout itself.
    base = config.getoption("basetemp")
    if base:
        directory = Path(base).resolve()
        if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
            raise pytest.UsageError(
                "--basetemp must be outside every Git checkout; workspace tests "
                "resolve ancestor Git roots. Use a new system-temp directory."
            )


_CLOSE_VERIFIED_READERS = pytest.StashKey()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    """Join test-owned readers before normal fixture/monkeypatch teardown.

    Use the teardown boundary rather than an autouse dependency on monkeypatch:
    forcing that fixture early would reorder unrelated runtime fixture cleanup.
    """
    close = item.stash.get(_CLOSE_VERIFIED_READERS, None)
    close_failure = None
    try:
        if close is not None:
            close()
    except Exception as error:  # noqa: BLE001 -- other fixture cleanup must still run
        close_failure = error
    outcome = yield
    if close_failure is not None:
        if outcome.excinfo is not None:
            close_failure = BaseExceptionGroup(
                "Reader and fixture teardown failures", [close_failure, outcome.excinfo[1]]
            )
        outcome.force_exception(close_failure)


@pytest.fixture(autouse=True)
def close_owned_verified_readers(request):
    """Own lazy readers even when a TestClient never enters ASGI lifespan."""
    from agentguard.api.verified_agents import VerifiedAgentReads

    created = []
    lock = threading.Lock()
    initialize = VerifiedAgentReads.__init__

    def track(reader, *args, **kwargs):
        initialize(reader, *args, **kwargs)
        with lock:
            created.append(reader)

    def close():
        failures = []
        while True:
            with lock:
                if not created:
                    break
                reader = created.pop()
            try:
                reader.close()
            except Exception as error:  # noqa: BLE001 -- close every owner, then fail teardown
                failures.append(error)
        if failures:
            raise ExceptionGroup("Test-owned verified readers failed to close", failures)

    request.node.stash[_CLOSE_VERIFIED_READERS] = close
    # A private patch context preserves the normal ordering of the test's own
    # monkeypatch fixture and all its dependent resources.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(VerifiedAgentReads, "__init__", track)
        try:
            yield
        finally:
            try:
                close()  # also catch readers created by another fixture's finalizer
            finally:
                del request.node.stash[_CLOSE_VERIFIED_READERS]


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
