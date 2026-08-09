"""AgentState Guard CLI — argparse-based entry point."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .commands.checkpoint import cmd_checkpoint
from .commands.diff import cmd_diff
from .commands.doctor import doctor as _doctor
from .commands.host_import import cmd_host_import
from .commands.restore import cmd_restore
from .commands.status import status as _status
from .core.config import Config
from .core.whitelist import Whitelist
from .policy.models import Decision, PolicyInput
from .recovery.service import RecoveryService
from .storage.db import StateDB
from .storage.snapshots import SnapshotStore
from .supervision.service import SupervisionService


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        result = _dispatch(args)
        _output(result, args.json)
        return 0 if _is_success(result) else 1
    except Exception as e:  # noqa: BLE001
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
    gc = sub.add_parser("gc", help="Garbage collection (dry-run)")
    gc.add_argument("--dry-run", action="store_true", default=True, help="Show what would be deleted")

    # handoff
    hf = sub.add_parser("handoff", help="Generate AI handoff document")
    hf.add_argument("--budget", type=int, default=2000, help="Token budget approximation")

    # incident
    sub.add_parser("incident", help="Generate incident bundle")

    # host-probe
    sub.add_parser("host-probe", help="Collect host state (stdin)")

    # ui
    ui_p = sub.add_parser("ui", help="Start the web UI")
    ui_p.add_argument("--port", type=int, default=8787, help="Port (default: 8787)")

    # serve
    sv = sub.add_parser("serve", help="Start API server")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--allow-remote", action="store_true")
    sv.add_argument("--host", type=str, default=None)

    # recovery truth workflow
    recovery = sub.add_parser("recovery", help="Inspect controlled recovery trust state")
    recovery_sub = recovery.add_subparsers(dest="recovery_subcommand", required=True)
    drill = recovery_sub.add_parser("drill", help="Inspect a recovery drill")
    drill_sub = drill.add_subparsers(dest="recovery_drill_subcommand", required=True)
    drill_show = drill_sub.add_parser("show", help="Show a recovery drill")
    drill_show.add_argument("drill_id", type=str)
    baseline = recovery_sub.add_parser("baseline", help="Manage trusted baseline lifecycle")
    baseline_sub = baseline.add_subparsers(dest="recovery_baseline_subcommand", required=True)
    baseline_create = baseline_sub.add_parser("create", help="Create a trusted baseline candidate")
    baseline_create.add_argument("--checkpoint-id", required=True)
    baseline_create.add_argument("--domain", required=True)
    baseline_approve = baseline_sub.add_parser("approve", help="Approve a baseline candidate")
    baseline_approve.add_argument("candidate_id", type=str)
    baseline_confirm = baseline_sub.add_parser("confirm", help="Confirm an approved baseline candidate")
    baseline_confirm.add_argument("candidate_id", type=str)
    baseline_confirm.add_argument("--authorization-id", required=True)
    baseline_confirm.add_argument("--nonce", required=True)
    baseline_retire = baseline_sub.add_parser("retire", help="Retire a trusted baseline")
    baseline_retire.add_argument("baseline_id", type=str)
    baseline_retire.add_argument("--reason-code", required=True)
    baseline_show = baseline_sub.add_parser("show", help="Show a baseline or candidate")
    baseline_show.add_argument("baseline_id", type=str)

    # supervise
    supervise = sub.add_parser("supervise", help="Run local supervision workflow")
    supervise_sub = supervise.add_subparsers(dest="supervise_subcommand", required=True)
    create = supervise_sub.add_parser("create", help="Create a supervised session")
    _add_supervision_policy_arguments(create)
    evaluate = supervise_sub.add_parser("evaluate", help="Evaluate structured local policy")
    _add_supervision_policy_arguments(evaluate)
    for action in ("show", "approve", "reject", "activate", "complete", "fail"):
        command = supervise_sub.add_parser(action, help=f"{action.capitalize()} a supervision session")
        command.add_argument("session_id", type=str)
        if action == "activate":
            command.add_argument("--checkpoint-id", type=str)

    return p


def _add_supervision_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--intent-kind", required=True)
    parser.add_argument("--effect-kind", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--evidence-ref", action="append", default=["cli-evidence"])
    parser.add_argument("--network-effect", choices=("true", "false"), required=True)
    parser.add_argument("--privilege-effect", choices=("true", "false"), required=True)
    parser.add_argument("--destructive-effect", choices=("true", "false"), required=True)
    parser.add_argument("--secret-access", choices=("true", "false"), required=True)
    parser.add_argument("--checkpoint-id")


def _supervision_policy_input(args: argparse.Namespace) -> PolicyInput:
    return PolicyInput(
        intent_kind=args.intent_kind,
        effect_kind=args.effect_kind,
        target_refs=tuple(args.target),
        execution_domain_id=args.domain or None,
        declared_scope=tuple(args.scope),
        requested_capabilities=(),
        network_effect=args.network_effect == "true",
        privilege_effect=args.privilege_effect == "true",
        destructive_effect=args.destructive_effect == "true",
        secret_access=args.secret_access == "true",
        evidence_refs=tuple(args.evidence_ref),
    )


def _supervision_result(session, decision: Decision | None = None, *, code: str | None = None) -> dict[str, Any]:
    result = {
        "supervision_session_id": session.supervision_session_id,
        "session_id": session.supervision_session_id,
        "status": session.status,
    }
    if decision is not None:
        result["decision"] = decision.value
    if code is not None:
        result["code"] = code
    return result


def _dispatch(args: argparse.Namespace) -> Any:
    base_dir = Path(args.directory or "/workspace/projects/agentstate-guard").resolve()
    config = Config(base_dir)
    cfg = {**config._data, "base_dir": str(config.base_dir)}

    db = StateDB(config.state_db())
    snapshots = SnapshotStore(config.snapshot_dir())

    if args.command == "recovery":
        db.connect()
        try:
            service = RecoveryService(database=db, snapshots=snapshots, adapters={})
            if args.recovery_subcommand == "drill":
                return service.show_drill(args.drill_id)
            if args.recovery_baseline_subcommand == "create":
                return service.create_trusted_baseline(
                    checkpoint_id=args.checkpoint_id,
                    execution_domain_id=args.domain,
                )
            if args.recovery_baseline_subcommand == "approve":
                return service.approve_trusted_baseline(args.candidate_id)
            if args.recovery_baseline_subcommand == "confirm":
                return service.confirm_trusted_baseline(
                    args.candidate_id,
                    args.authorization_id,
                    args.nonce,
                )
            if args.recovery_baseline_subcommand == "retire":
                return service.retire_trusted_baseline(args.baseline_id, args.reason_code)
            return service.show_trusted_baseline(args.baseline_id)
        finally:
            db.close()

    if args.command == "supervise":
        db.connect()
        try:
            service = SupervisionService(db, snapshots=snapshots)
            if args.supervise_subcommand in {"create", "evaluate"}:
                policy_input = _supervision_policy_input(args)
                session, decision, recovery_facts = service.create_authoritative(
                    args.intent_kind,
                    policy_input,
                    checkpoint_id=args.checkpoint_id,
                )
                result = _supervision_result(session, decision.decision)
                result.update(
                    {
                        "matched_rule_ids": list(decision.matched_rule_ids),
                        "summary_code": decision.summary_code,
                        "requires_manual_approval": decision.requires_manual_approval,
                        "requires_checkpoint": decision.requires_checkpoint,
                        "checkpoint_id": args.checkpoint_id,
                        "recovery_facts": recovery_facts.safe_summary(),
                    }
                )
                if decision.decision is Decision.BLOCK:
                    result["code"] = "POLICY_BLOCK"
                    return result
                if decision.decision is Decision.UNKNOWN:
                    result["code"] = "POLICY_UNKNOWN"
                    return result
                return result
            session = service._read(args.session_id)
            if args.supervise_subcommand == "show":
                return _supervision_result(session)
            if args.supervise_subcommand == "approve":
                session = service.approve(args.session_id)
            elif args.supervise_subcommand == "reject":
                session = service.reject(args.session_id)
            elif args.supervise_subcommand == "activate":
                before = session.status
                session = service.activate(args.session_id, args.checkpoint_id)
                if session.status == before and session.status == "REJECTED":
                    return _supervision_result(session, code="POLICY_BLOCK")
                if session.status == before and before == "AWAITING_APPROVAL":
                    return _supervision_result(session, code="APPROVAL_REQUIRED")
                if session.status == before and before in {"EVALUATED", "APPROVED"}:
                    return _supervision_result(session, code="CHECKPOINT_REQUIRED")
            elif args.supervise_subcommand == "complete":
                session = service.complete(args.session_id)
            elif args.supervise_subcommand == "fail":
                session = service.fail(args.session_id)
            return _supervision_result(session)
        finally:
            db.close()

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
        from .storage.blob import BlobStore
        from .storage.gc import execute_gc, plan_gc
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

    elif args.command == "ui":
        host = "127.0.0.1"
        port = args.port
        print(f"Starting AgentState Guard UI at http://{host}:{port}")
        print("Press Ctrl+C to stop")
        from .api.server import run_server
        run_server(host=host, port=port, config=cfg)
        return {"status": "stopped"}

    elif args.command == "serve":
        host = args.host or ("0.0.0.0" if args.allow_remote else "127.0.0.1")
        port = args.port
        if host == "0.0.0.0":
            print("⚠️  Remote access enabled. Ensure Tailscale or SSH tunnel is active.")
        print(f"Starting AgentState Guard API at http://{host}:{port}")
        from .api.server import run_server
        run_server(host=host, port=port, allow_remote=args.allow_remote, config=cfg)
        return {"status": "stopped"}

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


def _print_status(result: dict[str, Any]) -> None:
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
        return (
            not (result.get("status") == "FAILED" and "reason_code" in result)
            and "error" not in result
            and result.get("code") not in {
                "POLICY_BLOCK",
                "POLICY_UNKNOWN",
                "APPROVAL_REQUIRED",
                "CHECKPOINT_REQUIRED",
            }
        )
    if isinstance(result, list):
        return True
    return True


if __name__ == "__main__":
    sys.exit(main())
