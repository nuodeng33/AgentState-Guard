"""Compatibility entry point: live recovery requires the protection API."""

from ..core.whitelist import Whitelist
from ..storage.db import StateDB
from ..storage.snapshots import SnapshotStore


def cmd_restore(
    target_path: str,
    checkpoint_id: int,
    db: StateDB,
    snapshots: SnapshotStore,
    whitelist: Whitelist,
    restorable_paths: list,
    yes: bool = False,
) -> dict[str, object]:
    """Live writes require the protection preview and queued-operation flow."""
    return {
        "status": "error",
        "reason_code": "PREVIEW_REQUIRED",
        "message": "Restore requires a preview and queued recovery operation",
    }
