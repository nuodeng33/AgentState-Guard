"""Integrity-checked, offline Docker image artifact for volume recovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol

_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_HELPER_LABEL = "agentstate-guard-volume-helper-v1"


class _Completed(Protocol):
    returncode: int
    stdout: str


Run = Callable[..., _Completed]


@dataclass(frozen=True)
class BundledVolumeHelperArtifact:
    """A product-owned OCI/Docker archive with pinned bytes and image identity."""

    archive_path: Path
    archive_sha256: str
    image_reference: str
    image_id: str

    def __post_init__(self) -> None:
        if (
            _SHA256.fullmatch(self.archive_sha256) is None
            or _IMAGE_ID.fullmatch(self.image_id) is None
            or not self.image_reference.startswith("agentstate-guard/volume-helper:")
        ):
            raise ValueError("DOCKER_VOLUME_HELPER_METADATA_INVALID")

    @classmethod
    def default(cls) -> BundledVolumeHelperArtifact | None:
        override = os.environ.get("ASG_VOLUME_HELPER_ARCHIVE")
        metadata_override = os.environ.get("ASG_VOLUME_HELPER_METADATA")
        if override or metadata_override:
            if not override or not metadata_override:
                return None
            archive = Path(override)
            metadata_path = Path(metadata_override)
        else:
            resource_root = files("agentguard").joinpath("resources")
            archive = Path(str(resource_root.joinpath("docker-volume-helper.tar")))
            metadata_path = Path(
                str(resource_root.joinpath("docker-volume-helper.json"))
            )
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
                return None
            return cls(
                archive_path=archive,
                archive_sha256=str(metadata["archive_sha256"]),
                image_reference=str(metadata["image_reference"]),
                image_id=str(metadata["image_id"]),
            )
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def ensure_available(self, docker: str, run: Run) -> tuple[bool, str]:
        try:
            digest = hashlib.sha256(self.archive_path.read_bytes()).hexdigest()
        except OSError:
            return False, "DOCKER_VOLUME_HELPER_ARTIFACT_UNAVAILABLE"
        if digest != self.archive_sha256:
            return False, "DOCKER_VOLUME_HELPER_INTEGRITY_MISMATCH"

        inspected = self._inspect(docker, run)
        if inspected is None:
            loaded = run(
                [docker, "load", "--input", str(self.archive_path)], timeout=180
            )
            if loaded.returncode != 0:
                return False, "DOCKER_VOLUME_HELPER_LOAD_FAILED"
            inspected = self._inspect(docker, run)
        if inspected != (self.image_id, _HELPER_LABEL):
            return False, "DOCKER_VOLUME_HELPER_IDENTITY_MISMATCH"
        return True, "DOCKER_VOLUME_HELPER_VERIFIED"

    def _inspect(self, docker: str, run: Run) -> tuple[str, str] | None:
        result = run(
            [
                docker,
                "image",
                "inspect",
                self.image_reference,
                "--format",
                '{{.Id}}\t{{ index .Config.Labels "com.agentstate-guard.volume-helper" }}',
            ],
            timeout=30,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("\t")
        if len(parts) != 2:
            return None
        return parts[0], parts[1]


__all__ = ["BundledVolumeHelperArtifact"]
