"""Build the offline ASG named-volume helper image archive and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_BASE = (
    "python:3.11-slim@sha256:"
    "1042b61448fef4ba92d16a8c7eb4996d027568ce64792a7877fd88511e0af7c6"
)
IMAGE_REFERENCE = "agentstate-guard/volume-helper:v1"
LABEL = "agentstate-guard-volume-helper-v1"


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "docker helper artifact build failed")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--base-image", default=DEFAULT_BASE)
    args = parser.parse_args()
    if re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", args.base_image) is None:
        raise SystemExit("base image must be digest-pinned")

    repo = Path(__file__).resolve().parents[1]
    source = repo / "agentguard" / "recovery" / "docker_volume_helper.py"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive = args.output_dir / "docker-volume-helper.tar"
    metadata_path = args.output_dir / "docker-volume-helper.json"
    with tempfile.TemporaryDirectory(prefix="asg-volume-helper-build-") as temporary:
        context = Path(temporary)
        shutil.copy2(source, context / "docker_volume_helper.py")
        (context / "Dockerfile").write_text(
            "\n".join(
                [
                    f"FROM {args.base_image}",
                    f'LABEL com.agentstate-guard.volume-helper="{LABEL}"',
                    "COPY docker_volume_helper.py /asg-volume-helper.py",
                    'ENTRYPOINT ["python3", "/asg-volume-helper.py"]',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        _run(
            [
                args.docker,
                "build",
                "--pull=false",
                "--network=none",
                "--tag",
                IMAGE_REFERENCE,
                str(context),
            ]
        )
    image_id = _run(
        [args.docker, "image", "inspect", IMAGE_REFERENCE, "--format", "{{.Id}}"]
    )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise SystemExit("built helper image identity is invalid")
    _run([args.docker, "save", "--output", str(archive), IMAGE_REFERENCE])
    metadata = {
        "schema_version": 1,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "image_reference": IMAGE_REFERENCE,
        "image_id": image_id,
        "base_image": args.base_image,
        "identity_label": LABEL,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
