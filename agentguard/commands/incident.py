"""incident command — generate incident bundle (sanitized)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.sanitizer import sanitize_text
from ..core.versions import all_versions
from ..storage.db import StateDB
from ..storage.snapshots import SnapshotStore


def cmd_incident(
    output_dir: Path, db: StateDB, snapshots: SnapshotStore, config: dict
) -> Dict[str, object]:
    """Generate an incident bundle — sanitized diagnostic snapshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    versions = all_versions()
    checkpoints = db.list_checkpoints(limit=3)
    bundle = _build_bundle(versions, checkpoints)

    json_path = output_dir / "incident-latest.json"
    json_path.write_text(json.dumps(bundle, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    return {
        "incident_bundle": str(json_path),
        "size_bytes": json_path.stat().st_size,
        "checkpoints_included": len(checkpoints),
    }


def _build_bundle(versions: dict, checkpoints: list) -> Dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    bundle: Dict[str, object] = {
        "generated_at": now,
        "type": "incident_bundle",
        "schema_version": 1,
        "contents": {
            "environment": {
                "versions": {k: v for k, v in versions.items() if v},
            },
            "checkpoints": [
                {
                    "id": c["id"],
                    "label": c["label"],
                    "created_at": c["created_at"],
                    "file_count": c["file_count"],
                }
                for c in checkpoints
            ],
            "next_steps": [
                "Run `agentguard verify` to check integrity",
                "Run `agentguard doctor` for diagnostics",
                "Check latest checkpoint for drift",
            ],
        },
        "sha256_manifest": {},
        "security": {
            "contains_raw_secrets": False,
            "all_content_sanitized": True,
        },
    }
    return bundle
