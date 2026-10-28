"""Explicitly await a newly completed display-verification pass in API tests."""
import time
from datetime import UTC, datetime


def wait_for_verified(fetch, *, after=None, timeout=5):
    """Return the response only after a pass completed after this observation.

    A previously successful nonempty snapshot is insufficient. Failures return
    their fresh INVALID/ERROR metadata so callers retain strict business asserts.
    """
    after = after or datetime.now(UTC).isoformat()
    deadline = time.monotonic() + timeout
    while True:
        response = fetch()
        body = response[1] if isinstance(response, tuple) else response.json()
        verification = body.get("verification", {})
        state = verification.get("state")
        if ((verification.get("checked_at") or "") >= after
                and state in {"VERIFIED", "STALE", "INVALID", "ERROR"}):
            if state in {"VERIFIED", "STALE"}:
                assert (verification.get("verified_at") or "") >= after, body
            return response
        assert time.monotonic() < deadline, body
        time.sleep(0.01)


def read_verified(client, path, *, headers=None):
    after = datetime.now(UTC).isoformat()
    client.app.state.verified_agents.request_refresh()
    return wait_for_verified(lambda: client.get(path, headers=headers), after=after)
