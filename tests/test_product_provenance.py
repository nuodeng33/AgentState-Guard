"""Exact product provenance must survive non-editable packaging."""

from types import SimpleNamespace

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
