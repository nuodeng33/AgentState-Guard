"""Rollback coverage computation for transaction planning."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.whitelist import Whitelist
from ..core.snapshot import create_file_snapshot, restore_file_content


def compute_coverage(
    files_to_modify: List[str],
    whitelist: Whitelist,
    checkpoint_file_entries: Optional[Dict[str, dict]] = None,
) -> Dict[str, object]:
    """Compute rollback coverage for a set of files.

    Returns:
        coverage_pct: Percentage of files fully recoverable.
        fully_recoverable: List of files that can be fully rolled back.
        partially_recoverable: List of files with partial recovery.
        not_recoverable: List of files that cannot be recovered.
        reasons: Per-file reasons for any non-fully-recoverable status.
    """
    total = len(files_to_modify)
    if total == 0:
        return {
            "coverage_pct": 100.0,
            "fully_recoverable": [],
            "partially_recoverable": [],
            "not_recoverable": [],
            "reasons": {},
        }

    fully: List[str] = []
    partial: List[str] = []
    not_rec: List[str] = []
    reasons: Dict[str, str] = {}

    for fpath in files_to_modify:
        path = Path(fpath).resolve()
        file_reason = _check_file_recoverable(path, whitelist, checkpoint_file_entries or {})
        if file_reason == "ok":
            fully.append(fpath)
        elif file_reason.startswith("partial"):
            partial.append(fpath)
            reasons[fpath] = file_reason
        else:
            not_rec.append(fpath)
            reasons[fpath] = file_reason

    coverage_pct = round((len(fully) / total) * 100, 1) if total > 0 else 100.0

    return {
        "coverage_pct": coverage_pct,
        "coverage_label": _coverage_label(coverage_pct),
        "fully_recoverable": fully,
        "partially_recoverable": partial,
        "not_recoverable": not_rec,
        "reasons": reasons,
    }


def _coverage_label(pct: float) -> str:
    if pct == 100.0:
        return "fully_recoverable"
    if pct >= 50.0:
        return "partially_recoverable"
    return "not_recoverable"


def _check_file_recoverable(
    path: Path,
    whitelist: Whitelist,
    checkpoint_entries: Dict[str, dict],
) -> str:
    """Check if a single file is recoverable. Returns reason string or 'ok'."""
    # Must be in whitelist
    if not whitelist.is_allowed(path):
        return "not in restore whitelist"

    # Must not be a symlink
    if path.is_symlink():
        return "cannot restore symlinks"

    # Must have been snapshotted as restorable
    str_path = str(path)
    entry = checkpoint_entries.get(str_path)
    if not entry:
        return "no checkpoint entry for this file"

    mode = entry.get("mode", "audit_only")
    if mode != "restorable":
        return f"file saved as {mode}, cannot restore content"

    # Must have readable content in blob/snapshot
    if not entry.get("content_gz") and not entry.get("blob_sha256"):
        return "no content stored in checkpoint"

    return "ok"


def coverage_summary(coverage: Dict[str, object]) -> str:
    """Return a human-readable summary of coverage."""
    pct = coverage.get("coverage_pct", 0)
    label = coverage.get("coverage_label", "unknown")
    f = len(coverage.get("fully_recoverable", []))
    p = len(coverage.get("partially_recoverable", []))
    n = len(coverage.get("not_recoverable", []))
    total = f + p + n
    return f"{label} ({pct}%): {f}/{total} files fully recoverable, {p} partial, {n} not recoverable"
