"""ASDL/1 Pairing State Machine — injectable Clock + RandomSource."""

from enum import Enum

from .crypto import (
    Clock,
    RandomSource,
    SecureRandom,
    SystemClock,
    build_pairing_transcript,
    derive_sas,
    format_sas,
    random_bytes,
)


class PairState(str, Enum):
    CREATED = "created"
    FIRST_CONNECTION = "first_connection"
    SAS_PENDING = "sas_pending"
    CONFIRMED_BOTH = "confirmed_both"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {
    PairState.CONSUMED,
    PairState.EXPIRED,
    PairState.REJECTED,
    PairState.FAILED,
    PairState.CANCELLED,
}

# Complete transition table
ALLOWED_TRANSITIONS: dict[PairState, set] = {
    PairState.CREATED: {
        PairState.FIRST_CONNECTION,
        PairState.EXPIRED,
        PairState.REJECTED,
        PairState.CANCELLED,
    },
    PairState.FIRST_CONNECTION: {
        PairState.SAS_PENDING,
        PairState.EXPIRED,
        PairState.REJECTED,
        PairState.CANCELLED,
    },
    PairState.SAS_PENDING: {
        PairState.CONFIRMED_BOTH,
        PairState.FAILED,
        PairState.EXPIRED,
        PairState.REJECTED,
        PairState.CANCELLED,
    },
    PairState.CONFIRMED_BOTH: {
        PairState.CONSUMED,
        PairState.EXPIRED,
        PairState.CANCELLED,
    },
    # ALL terminal states accept no further transitions:
    PairState.CONSUMED: set(),
    PairState.EXPIRED: set(),
    PairState.REJECTED: set(),
    PairState.FAILED: set(),
    PairState.CANCELLED: set(),
}


def is_valid_transition(current: PairState, target: PairState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


class PairingSession:
    """A pairing session with injectable clock and random source."""

    def __init__(
        self,
        session_id: str,
        desktop_uuid: str,
        desktop_pubkey_der: bytes,
        desktop_tls_spki_fp: str,
        expiry_seconds: int = 120,
        max_sas_attempts: int = 3,
        clock: Clock = None,
        rng: RandomSource = None,
    ):
        self.session_id = session_id
        self.desktop_uuid = desktop_uuid
        self.desktop_pubkey_der = desktop_pubkey_der
        self.desktop_tls_spki_fp = desktop_tls_spki_fp
        self._clock = clock or SystemClock()
        self._rng = rng or SecureRandom()
        self.pairing_secret = random_bytes(32, self._rng)
        self.nonce_desktop = random_bytes(32, self._rng)
        self.nonce_android: bytes | None = None
        self.android_uuid: str | None = None
        self.android_pubkey_der: bytes | None = None
        self._created_at = self._clock.now()
        self.expiry_seconds = expiry_seconds
        self._expiry_abs = self._created_at + expiry_seconds
        self.state = PairState.CREATED
        self.sas_attempts = 0
        self.max_sas_attempts = max_sas_attempts

    @property
    def is_expired(self) -> bool:
        return self._clock.now() >= self._expiry_abs

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def _clear_secret_if_terminal(self) -> None:
        if self.state in TERMINAL_STATES:
            self.pairing_secret = b""

    def expire_if_needed(self) -> bool:
        """Enter the absorbing expiry state and clear secret material once due."""
        if self.is_expired and not self.is_terminal:
            self.state = PairState.EXPIRED
            self._clear_secret_if_terminal()
            return True
        return False

    def _transition(self, target: PairState, force_expiry_check: bool = True) -> None:
        if force_expiry_check and self.expire_if_needed():
            raise ValueError("Pairing session expired")
        if self.is_terminal:
            raise ValueError(
                f"Terminal state {self.state.value}: cannot transition to {target.value}"
            )
        if not is_valid_transition(self.state, target):
            raise ValueError(f"Invalid transition: {self.state.value} → {target.value}")

    def set_state(self, new_state: PairState) -> None:
        """Generic transition through the validation table."""
        self._transition(new_state)
        self.state = new_state
        self._clear_secret_if_terminal()

    def first_connection(self, android_uuid: str, nonce_android: bytes) -> None:
        self._transition(PairState.FIRST_CONNECTION)
        if self.is_terminal:
            raise ValueError(f"Cannot transition from {self.state.value}")
        if self.is_expired:
            self._transition(PairState.EXPIRED)
            raise ValueError("Pairing session expired")
        self.android_uuid = android_uuid
        self.nonce_android = nonce_android
        self.state = PairState.FIRST_CONNECTION

    def set_android_pubkey(self, pubkey_der: bytes) -> None:
        if self.expire_if_needed():
            raise ValueError("Pairing session expired")
        if self.state != PairState.FIRST_CONNECTION:
            raise ValueError(
                f"Invalid transition: {self.state.value} → set_android_pubkey"
            )
        if (
            self.android_pubkey_der is not None
            and self.android_pubkey_der != pubkey_der
        ):
            raise ValueError("Android public key cannot be replaced")
        self.android_pubkey_der = pubkey_der

    def start_sas(self) -> str:
        self._transition(PairState.SAS_PENDING)
        if (
            not self.android_uuid
            or self.nonce_android is None
            or self.android_pubkey_der is None
        ):
            raise ValueError("Pairing identity is incomplete")
        transcript = build_pairing_transcript(
            1,
            self.session_id,
            self.desktop_uuid,
            self.android_uuid,
            self.desktop_pubkey_der,
            self.android_pubkey_der,
            self.desktop_tls_spki_fp,
            self.nonce_desktop,
            self.nonce_android,
            int(self._expiry_abs),
        )
        sas = derive_sas(self.pairing_secret, transcript)
        self.state = PairState.SAS_PENDING
        return format_sas(sas)

    def confirm(self) -> None:
        self._transition(PairState.CONFIRMED_BOTH)
        if self.is_terminal:
            raise ValueError(f"Session in terminal state {self.state.value}")
        self.state = PairState.CONFIRMED_BOTH

    def consume(self) -> None:
        self._transition(PairState.CONSUMED)
        if self.is_terminal:
            raise ValueError(f"Session in terminal state {self.state.value}")
        self.state = PairState.CONSUMED
        self._clear_secret_if_terminal()

    def reject(self) -> None:
        self._transition(PairState.REJECTED)
        self.state = PairState.REJECTED
        self._clear_secret_if_terminal()

    def cancel(self) -> None:
        self._transition(PairState.CANCELLED)
        self.state = PairState.CANCELLED
        self._clear_secret_if_terminal()

    def fail(self) -> None:
        if self.expire_if_needed():
            raise ValueError("Pairing session expired")
        if self.state != PairState.SAS_PENDING:
            raise ValueError(f"Invalid transition: {self.state.value} → failed")
        self.sas_attempts += 1
        if self.sas_attempts >= self.max_sas_attempts:
            self._transition(PairState.FAILED, force_expiry_check=False)
            self.state = PairState.FAILED
            self._clear_secret_if_terminal()


class PairingManager:
    """Minimal manager for active pairing sessions."""

    def __init__(self, max_sessions: int = 5):
        self._sessions: dict[str, PairingSession] = {}
        self._max = max_sessions

    def create_session(
        self,
        desktop_uuid: str,
        desktop_pubkey_der: bytes,
        desktop_tls_spki_fp: str,
        expiry_seconds: int = 120,
        max_sas_attempts: int = 3,
        clock: Clock = None,
        rng: RandomSource = None,
    ) -> PairingSession:
        for session in self._sessions.values():
            session.expire_if_needed()
        to_del = [sid for sid, s in self._sessions.items() if s.is_terminal]
        for sid in to_del:
            del self._sessions[sid]
        if len(self._sessions) >= self._max:
            raise ValueError("Maximum active pairing sessions reached")
        sid = (rng or SecureRandom()).bytes(16).hex()
        session = PairingSession(
            sid,
            desktop_uuid,
            desktop_pubkey_der,
            desktop_tls_spki_fp,
            expiry_seconds=expiry_seconds,
            max_sas_attempts=max_sas_attempts,
            clock=clock,
            rng=rng,
        )
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> PairingSession | None:
        s = self._sessions.get(session_id)
        if s:
            s.expire_if_needed()
        return s

    def active_sessions(self) -> int:
        for session in self._sessions.values():
            session.expire_if_needed()
        to_del = [sid for sid, s in self._sessions.items() if s.is_terminal]
        for sid in to_del:
            del self._sessions[sid]
        return len(self._sessions)

    def cancel_all(self) -> None:
        """Cancel and forget every transient pairing session."""
        try:
            for session in self._sessions.values():
                if not session.is_terminal:
                    try:
                        session.cancel()
                    except ValueError:
                        # Expiry is also terminal and clears the pairing secret.
                        continue
        finally:
            self._sessions.clear()
