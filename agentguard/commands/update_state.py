"""update-state command — maintain state documents from structured data only."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.docker import container_running, docker_available
from ..core.versions import all_versions
from ..storage.db import StateDB


def cmd_update_state(doc_dir: Path, db: StateDB, config: dict) -> dict[str, object]:
    """Update docs/CURRENT_STATE.md, NEXT_STEPS.md, DECISIONS.md, CHANGELOG.md.

    Only uses structured data — no LLM-generated content.
    """
    doc_dir.mkdir(parents=True, exist_ok=True)

    checkpoints = db.list_checkpoints(limit=5)
    versions = all_versions()
    docker_ok = docker_available()

    updated = []

    # CURRENT_STATE.md
    current = _render_current_state(versions, docker_ok, config, checkpoints)
    (doc_dir / "CURRENT_STATE.md").write_text(current, encoding="utf-8")
    updated.append("docs/CURRENT_STATE.md")

    # NEXT_STEPS.md
    next_steps = _render_next_steps(checkpoints)
    (doc_dir / "NEXT_STEPS.md").write_text(next_steps, encoding="utf-8")
    updated.append("docs/NEXT_STEPS.md")

    # DECISIONS.md
    decisions_path = doc_dir / "DECISIONS.md"
    if not decisions_path.exists():
        decisions_path.write_text("# Decisions\n\n*No decisions recorded yet.*\n", encoding="utf-8")
    updated.append("docs/DECISIONS.md")

    # CHANGELOG.md
    changelog_path = doc_dir / "CHANGELOG.md"
    if not changelog_path.exists():
        _init_changelog(changelog_path)
    updated.append("docs/CHANGELOG.md")

    return {"updated_files": updated}


def _render_current_state(
    versions: dict[str, str | None],
    docker_ok: bool,
    config: dict,
    checkpoints: list[dict[str, Any]],
) -> str:
    """Render current environment state as Markdown."""
    lines = [
        "# Current State",
        "",
        f"_Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "",
        "## Software Versions",
        "",
    ]
    for tool, ver in sorted(versions.items()):
        v = ver or "not found"
        lines.append(f"- **{tool}:** {v}")

    lines.extend([
        "",
        "## Docker",
        "",
        f"- Available: {'✅' if docker_ok else '❌'}",
    ])

    # Named-container fact only for an explicitly configured container;
    # Docker capability above is independent of any historical default name.
    # Tolerant read: legacy flat keys and the nested [checks] table both count.
    configured_container = config.get("container_name") or (
        config.get("checks") or {}
    ).get("container_name")
    if docker_ok and configured_container:
        running, info = container_running(configured_container)
        lines.append(f"- Container: {'✅ running' if running else '❌ ' + str(info)}")

    lines.extend([
        "",
        "## Checkpoints",
        "",
    ])
    if checkpoints:
        lines.append("| # | Label | Date |")
        lines.append("|---|-------|------|")
        for cp in checkpoints:
            lines.append(f"| {cp['id']} | {cp['label']} | {cp['created_at'][:19]} |")
    else:
        lines.append("*No checkpoints yet.*")

    lines.append("")
    return "\n".join(lines)


def _render_next_steps(checkpoints: list[dict[str, Any]]) -> str:
    """Render suggested next actions from checkpoint data."""
    lines = [
        "# Next Steps",
        "",
        f"_Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "",
        "## Recommended Actions",
        "",
    ]

    if not checkpoints:
        lines.append("- Run `agentguard checkpoint` to establish a baseline")
        lines.append("- Run `agentguard doctor` for a full diagnostic")

    if len(checkpoints) < 2:
        lines.append("- Take another checkpoint after significant config changes")
    else:
        latest = checkpoints[0]
        lines.append(f"- Latest checkpoint: #{latest['id']} ({latest['label']})")
        lines.append("- Run `agentguard diff` to check for drift")

    lines.extend([
        "",
        "## Regular Maintenance",
        "",
        "- Run `agentguard status` to quick-check the environment",
        "- Run `agentguard doctor` periodically for deep diagnostics",
        "- Run `agentguard report` before/after major changes",
        "",
    ])
    return "\n".join(lines)


def _init_changelog(path: Path) -> None:
    """Initialize CHANGELOG.md with first entry."""
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    path.write_text(
        f"# Changelog\n\n"
        f"## {now}\n\n"
        f"- Initialized AgentState Guard v0.1.0\n",
        encoding="utf-8",
    )
