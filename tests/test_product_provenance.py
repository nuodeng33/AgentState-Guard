"""Exact product provenance must survive non-editable packaging."""

from types import SimpleNamespace

import pytest

from agentguard.core import versions


def test_exact_product_sha_uses_embedded_build_sha_without_checkout(monkeypatch):
    product_sha = "a" * 40
    monkeypatch.setattr(versions, "_EMBEDDED_PRODUCT_SHA", product_sha, raising=False)
    monkeypatch.setattr(
        versions,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(success=False, stdout=""),
    )

    assert versions.exact_product_sha() == product_sha


def test_exact_product_sha_rejects_invalid_embedded_build_sha(monkeypatch):
    monkeypatch.setattr(versions, "_EMBEDDED_PRODUCT_SHA", "not-a-sha", raising=False)
    monkeypatch.setattr(
        versions,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(success=False, stdout=""),
    )

    assert versions.exact_product_sha() is None


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_embedded_product_sha_survives_unavailable_git(monkeypatch, error, tmp_path):
    product_sha = "b" * 40
    monkeypatch.setattr(versions, "_EMBEDDED_PRODUCT_SHA", product_sha)

    def unavailable(*_args, **_kwargs):
        raise error("git unavailable")

    monkeypatch.setattr(versions, "run_command", unavailable)
    assert versions.exact_product_sha() == product_sha
    assert versions.exact_product_sha(cwd=tmp_path) is None
