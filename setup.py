"""Minimal build hook that embeds exact source provenance in distributions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

_ROOT = Path(__file__).resolve().parent
_PROVENANCE = Path("agentguard/core/_build_provenance.py")
_EXACT_SHA = re.compile(r"[0-9a-f]{40}")


def _source_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None:
        candidate = result.stdout.strip()
        if result.returncode == 0 and _EXACT_SHA.fullmatch(candidate):
            return candidate
    source = _ROOT / _PROVENANCE
    if source.is_file():
        match = re.search(r'PRODUCT_SHA\s*=\s*["\']([0-9a-f]{40})["\']', source.read_text())
        if match:
            return match.group(1)
    raise RuntimeError("exact product SHA unavailable for distribution build")


def _write_provenance(root: Path, product_sha: str) -> None:
    target = root / _PROVENANCE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '"""Build-generated exact product provenance."""\n\n'
        f'PRODUCT_SHA = "{product_sha}"\n',
        encoding="utf-8",
    )


class ProvenanceBuildPy(build_py):
    def run(self) -> None:
        super().run()
        _write_provenance(Path(self.build_lib), _source_sha())


class ProvenanceSdist(sdist):
    def make_release_tree(self, base_dir, files) -> None:
        super().make_release_tree(base_dir, files)
        _write_provenance(Path(base_dir), _source_sha())


setup(cmdclass={"build_py": ProvenanceBuildPy, "sdist": ProvenanceSdist})
