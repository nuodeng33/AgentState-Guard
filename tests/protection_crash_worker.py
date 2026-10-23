"""Test-only child: pause at an existing recovery fault callback until OS termination."""

import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentguard.recovery.protection import ProtectionService
from agentguard.recovery.protection_domains import DomainRegistry


def main():
    state, operation_id, point, destination = sys.argv[1:]
    state, destination = Path(state), Path(destination)

    def checkpoint(at, current_id, step_id=None):
        if point == 'after_compensation' and at == 'before_complete':
            raise RuntimeError('TEST_FORWARD_FAILURE_BEFORE_COMPENSATION')
        if at == point:
            pending = destination.with_suffix('.pending')
            pending.write_text(json.dumps({'pid': os.getpid(), 'operation_id': current_id,
                                           'step_id': step_id, 'point': at}), encoding='utf-8')
            pending.replace(destination)
            threading.Event().wait()

    domains = DomainRegistry(state)
    instance = ProtectionService(state / 'state.db', state, domains, fault=checkpoint)
    try:
        instance.execute(operation_id)
        raise RuntimeError('TEST_CHECKPOINT_NOT_REACHED')
    finally:
        instance.close()
        domains.close()


if __name__ == '__main__':
    main()
