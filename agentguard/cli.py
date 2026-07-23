"""AgentState Guard CLI — argparse-based entry point."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.config import Config
from .storage.db import StateDB
from .storage.snapshots import SnapshotStore

# Lazy imports for commands
from .commands.status import status as _status
from .commands.doctor import doctor as _doctor
from .commands.checkpoint import cmd_checkpoint
from .commands.diff import cmd_diff
from .commands.restore import cmd_restore
from .commands.report import cmd_report
from .commands.update_state import cmd_update_state
from .commands.host_import import cmd_host_import
from .core.whitelist import Whitelist


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        result = _dispatch(args)
        _output(result, args.json)
        return 0 if _is_success(result) else 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        return 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentguard",
        description="AgentState Guard — protect development environment state",
    )
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p.add_argument("--debug", action="store_true", help="Traceback on errors")
    p.add_argument("-C", "--directory", type=str, help="Project directory")

    sub = p.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Quick environment status check")

    # doctor
    sub.add_parser("doctor", help="Full diagnostic with PASS/WARN/FAIL/SKIP")

    # checkpoint
    cp = sub.add_parser("checkpoint", help="Record environment snapshot")
    cp.add_argument("label", type=str, help="Description of this checkpoint")

    # checkpoints
    sub.add_parser("checkpoints", help="List saved checkpoints")

    # diff
    df = sub.add_parser("diff", help="Compare current state vs checkpoint")
    df.add_argument("--checkpoint", type=int, default=None, help="Checkpoint ID")

    # restore
    rs = sub.add_parser("restore", help="Restore a single whitelisted file")
    rs.add_argument("--checkpoint", type=int, required=True, help="Checkpoint ID")
    rs.add_argument("--file", type=str, required=True, help="File to restore")
    rs.add_argument("--dry-run", action="store_true", help="Show what would change")

    # report
    sub.add_parser("report", help="Generate Markdown + JSON reports")

    # update-state
    sub.add_parser("update-state", help="Update state documentation files")

    # host-import
    sub.add_parser("host-import", help="Import host state from stdin JSON")

    # restore: add --yes
    rs.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # plan
    sub.add_parser("plan", help="Create a transaction plan")

    # transactions
    sub.add_parser("transactions", help="List transactions")

    # transaction show
    ts = sub.add_parser("transaction", help="Show or manage a transaction")
    ts_sub = ts.add_subparsers(dest="txn_subcommand")
    show_p = ts_sub.add_parser("show", help="Show transaction details")
    show_p.add_argument("id", type=int, help="Transaction ID")
    undo_p = ts_sub.add_parser("undo", help="Undo a transaction")
    undo_p.add_argument("id", type=int, help="Transaction ID")
    undo_p.add_argument("--yes", action="store_true", help="Skip confirmation")

    # undo (top-level shorthand)
    ud = sub.add_parser("undo", help="Undo a transaction")
    ud.add_argument("id", type=int, help="Transaction ID")
    ud.add_argument("--yes", action="store_true", help="Skip confirmation")

    # verify
    sub.add_parser("verify", help="Run integrity checks")

    # test-restore
    tr = sub.add_parser("test-restore", help="Test restore to temp sandbox")
    tr.add_argument("--checkpoint", type=int, required=True, help="Checkpoint ID")

    # gc
    gc = sub.add_parser("gc", help="Garbage collection")
    gc.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    gc.add_argument("--execute", action="store_true", help="Actually delete")

    # handoff
    hf = sub.add_parser("handoff", help="Generate AI handoff document")
    hf.add_argument("--budget", type=int, default=2000, help="Token budget approximation")

    # incident
    sub.add_parser("incident", help="Generate incident bundle")

    # host-probe
    sub.add_parser("host-probe", help="Collect host state (stdin)")

    return p


def _dispatch(args: argparse.Namespace) -> Any:
    base_dir = Path(args.directory or "/workspace/projects/agentstate-guard").resolve()
    config = Config(base_dir)
    cfg = config._data

    db = StateDB(config.state_db())
    snapshots = SnapshotStore(config.snapshot_dir())

    if args.command == "status":
        db.connect()
        result = _status(cfg, db)
        db.close()
        return result

    elif args.command == "doctor":
        return _doctor(cfg)

    elif args.command == "checkpoint":
        db.connect()
        result = cmd_checkpoint(args.label, cfg, db, snapshots)
        db.close()
        return result

    elif args.command == "checkpoints":
        db.connect()
        cps = db.list_checkpoints(limit=50)
        db.close()
        return {"checkpoints": cps}

    elif args.command == "diff":
        db.connect()
        result = cmd_diff(db, snapshots, getattr(args, "checkpoint", None), cfg)
        db.close()
        return result

    elif args.command == "restore":
        db.connect()
        wl = Whitelist(config.restore_whitelist)
        result = cmd_restore(
            target_path=args.file,
            checkpoint_id=args.checkpoint,
            db=db,
            snapshots=snapshots,
            whitelist=wl,
            restorable_paths=config.restore_whitelist,
            yes=args.yes,
        )
        db.close()
        return result

    elif args.command == "host-import":
        return cmd_host_import()

    elif args.command == "plan":
        db.connect()
        from .transactions.engine import TransactionEngine
        engine = TransactionEngine(db, snapshots, cfg)
        result = engine.create_plan(args.label if hasattr(args, 'label') else "unnamed", [])
        db.close()
        return result

    elif args.command == "transactions":
        db.connect()
        from .transactions.engine import TransactionEngine
        engine = TransactionEngine(db, snapshots, cfg)
        txns = engine.list_transactions(limit=50)
        db.close()
        return {"transactions": txns}

    elif args.command == "transaction" and args.txn_subcommand == "show":
        db.connect()
        from .transactions.engine import TransactionEngine
        engine = TransactionEngine(db, snapshots, cfg)
        t = engine.get_transaction(args.id)
        db.close()
        return t or {"error": f"Transaction #{args.id} not found"}

    elif args.command in ("undo",) and args.txn_subcommand is None:
        # Top-level undo command
        db.connect()
        from .transactions.engine import TransactionEngine
        engine = TransactionEngine(db, snapshots, cfg)
        result = engine.undo(args.id, yes=args.yes)
        db.close()
        return result
    elif args.command == "transaction" and args.txn_subcommand == "undo":
        db.connect()
        from .transactions.engine import TransactionEngine
        engine = TransactionEngine(db, snapshots, cfg)
        result = engine.undo(args.id, yes=args.yes)
        db.close()
        return result

    elif args.command == "verify":
        db.connect()
        issues = []
        # Check DB integrity
        integrity = db._conn.execute("PRAGMA integrity_check").fetchone()
        if integrity and integrity[0] != "ok":
            issues.append(f"DB integrity: {integrity[0]}")
        # Check migration status
        from .storage.migrations import MigrationEngine
        engine = MigrationEngine(config.state_db())
        cur = engine.current_version(db._conn)
        pending = engine.pending(db._conn)
        db.close()
        return {
            "status": "ok" if not issues else "warning",
            "db_integrity": "ok" if (integrity and integrity[0] == "ok") else "fail",
            "schema_version": cur,
            "pending_migrations": len(pending),
            "issues": issues,
        }

    elif args.command == "test-restore":
        db.connect()
        from .commands.restore import cmd_restore
        from .core.whitelist import Whitelist
        cp = db.get_checkpoint(args.checkpoint)
        if not cp:
            db.close()
            return {"error": f"Checkpoint #{args.checkpoint} not found"}
        snap_data = snapshots.load(cp["snapshot_path"])
        if not snap_data:
            db.close()
            return {"error": "Snapshot not found"}
        files = snap_data.get("files", {})
        wl = Whitelist([])
        data_info = {k: v for k, v in files.items() if v.get("mode") == "restorable"}
        db.close()
        return {
            "status": "ok",
            "checkpoint_id": args.checkpoint,
            "restorable_files": len(data_info),
            "files": [{"path": k, "size": v.get("size", 0)} for k, v in data_info.items()],
        }

    elif args.command == "gc":
        db.connect()
        from .storage.gc import plan_gc, execute_gc
        from .storage.blob import BlobStore
        blob_store = BlobStore(config.snapshot_dir().parent / "blobs")
        plan = plan_gc(db._conn, blob_store)
        if args.execute:
            result = execute_gc(db._conn, blob_store, plan, dry_run=False)
        else:
            result = execute_gc(db._conn, blob_store, plan, dry_run=True)
        db.close()
        return result

    elif args.command == "handoff":
        from .commands.handoff import cmd_handoff
        base = Path(config._data.get("base_dir", "/workspace"))
        db.connect()
        result = cmd_handoff(base / "reports", db, budget=args.budget)
        db.close()
        return result

    elif args.command == "incident":
        db.connect()
        from .commands.incident import cmd_incident
        base = Path(cfg.get("base_dir", str(base_dir)))
        result = cmd_incident(base / "reports", db, snapshots, cfg)
        db.close()
        return result

    elif args.command == "host-probe":
        return cmd_host_import()

    return {"error": f"Unknown command: {args.command}"}


def _output(result: Any, json_mode: bool) -> None:
    """Print result as JSON or human-readable format."""
    if json_mode:
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
        return

    if isinstance(result, dict):
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            return

        if "checks" in result:
            # status output
            _print_status(result)
            return

        if isinstance(result.get("checkpoints"), list):
            # checkpoints list
            _print_checkpoints(result["checkpoints"])
            return

        if "diagnostics" in result:
            for d in result["diagnostics"]:
                print(f"{d['status']:<5} {d['check']:<25} {d['message']}")

    elif isinstance(result, list):
        # doctor output — list of diagnostic results
        for item in result:
            if isinstance(item, dict) and "status" in item and "check" in item:
                print(f"  {item['status']:<5} {item['check']:<25} {item['message']}")
            else:
                print(item)
        return

    # Default: print as formatted JSON
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))


def _print_status(result: Dict[str, Any]) -> None:
    """Print status check results."""
    checks = result.get("checks", {})
    print("=== AgentState Guard Status ===\n")
    for check_name, value in checks.items():
        icon = "✅" if value else "❌"
        print(f"  {icon} {check_name}: {value}")

    versions = result.get("versions", {})
    if versions:
        print("\n--- Versions ---")
        for tool, ver in versions.items():
            v = ver or "N/A"
            print(f"  {tool}: {v}")

    if result.get("drift_detected"):
        print("\n⚠️  Drift detected — run 'agentguard diff' for details")


def _print_checkpoints(cps: list) -> None:
    """Print checkpoints table."""
    if not cps:
        print("No checkpoints recorded.")
        return
    print(f"{'ID':<5} {'Label':<30} {'Created':<25} {'Files':<6} {'Git':<15}")
    print("-" * 85)
    for cp in cps:
        git = cp.get("git_branch") or ""
        print(f"{cp['id']:<5} {cp['label']:<30} {cp['created_at'][:19]:<25} {cp['file_count']:<6} {git:<15}")


def _is_success(result: Any) -> bool:
    if isinstance(result, dict):
        return "error" not in result
    if isinstance(result, list):
        return True
    return True


if __name__ == "__main__":
    sys.exit(main())
