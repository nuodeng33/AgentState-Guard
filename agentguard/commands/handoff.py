"""handoff command — generate AI handoff document."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.docker import docker_available
from ..core.versions import all_versions
from ..storage.db import StateDB


def cmd_handoff(output_dir: Path, db: StateDB, budget: int = 2000) -> Dict[str, object]:
    """Generate AI handoff document with token budget.

    Args:
        output_dir: Directory for reports.
        db: StateDB instance.
        budget: Approximate text budget in tokens (chars/4).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    versions = all_versions()
    docker_ok = docker_available()
    checkpoints = db.list_checkpoints(limit=5)
    txns = []
    if db._conn:
        try:
            txns = db._conn.execute(
                "SELECT id, status, label, created_at FROM transactions ORDER BY id DESC LIMIT 5"
            ).fetchall()
        except Exception:
            pass

    data = _build_handoff_data(versions, docker_ok, checkpoints, txns, budget)
    json_path = output_dir / "handoff-latest.json"
    md_path = output_dir / "handoff-latest.md"
    json_path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_markdown(data), encoding="utf-8")

    return {
        "handoff_json": str(json_path),
        "handoff_md": str(md_path),
        "budget": budget,
        "checkpoint_count": len(checkpoints),
    }


def _build_handoff_data(
    versions: dict, docker_ok: bool, checkpoints: list, txns: list, budget: int
) -> Dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": now,
        "budget": budget,
        "environment": {
            "docker_available": docker_ok,
            "versions": {k: v for k, v in versions.items() if v},
        },
        "checkpoints": [
            {"id": c["id"], "label": c["label"], "created_at": c["created_at"]}
            for c in checkpoints
        ],
        "transactions": [
            {"id": t[0], "status": t[1], "label": t[2], "created_at": t[3]}
            for t in txns
        ],
        "next_steps": [
            "Run `agentguard doctor` for full diagnostic",
            "Run `agentguard diff` to check for drift",
            "Run `agentguard verify` for integrity check",
        ],
    }


def _render_markdown(data: Dict[str, object]) -> str:
    lines = [
        "# AI Handoff",
        "",
        f"_Generated: {data.get('generated_at', 'unknown')}_",
        f"_Budget: {data.get('budget', 2000)} tokens (approx)_",
        "",
        "## Environment",
        "",
    ]
    env = data.get("environment", {})
    for tool, ver in (env.get("versions", {}) or {}).items():
        lines.append(f"- **{tool}:** {ver}")
    lines.append("")
    lines.append("## Checkpoints")
    for cp in data.get("checkpoints", []):
        lines.append(f"- #{cp['id']}: {cp['label']} ({cp['created_at'][:19]})")
    lines.append("")
    lines.append("## Transactions")
    for t in data.get("transactions", []):
        lines.append(f"- #{t['id']}: {t['label']} [{t['status']}]")
    lines.append("")
    lines.append("## Next Steps")
    for s in data.get("next_steps", []):
        lines.append(f"- {s}")
    lines.append("")
    return "\n".join(lines)
