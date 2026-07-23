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

    elif args.command == "report":
        db.connect()
        result = cmd_report(base_dir / "reports", db, cfg)
        db.close()
        return result

    elif args.command == "update-state":
        db.connect()
        result = cmd_update_state(base_dir / "docs", db, cfg)
        db.close()
        return result

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
