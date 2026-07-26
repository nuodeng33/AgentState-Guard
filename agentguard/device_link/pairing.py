"""ASDL/1 pairing state machine with injectable time and randomness."""

from __future__ import annotations

import threading
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
from .errors import DeviceLinkError, state_conflict


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

ALLOWED_TRANSITIONS: dict[PairState, set[PairState]] = {
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
    PairState.CONSUMED: set(),
    PairState.EXPIRED: set(),
    PairState.REJECTED: set(),
    PairState.FAILED: set(),
    PairState.CANCELLED: set(),
}


def is_valid_transition(current: PairState, target: PairState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


class PairingSession:
    """One pairing session with monotonic terminal-state enforcement."""

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
        self._lock = threading.RLock()

    @property
    def is_expired(self) -> bool:
        return self._clock.now() >= self._expiry_abs

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def _transition(
        self,
        target: PairState,
        *,
        force_expiry_check: bool = True,
    ) -> None:
        if force_expiry_check and self.is_expired and not self.is_terminal:
            self.state = PairState.EXPIRED
            if target == PairState.EXPIRED:
                return
            raise DeviceLinkError(
                410,
                "PAIR_SESSION_EXPIRED",
                "Pairing session expired",
            )
        if self.is_terminal:
            raise state_conflict(
                f"Terminal state {self.state.value} cannot transition"
            )
        if not is_valid_transition(self.state, target):
            raise state_conflict(
                f"Invalid transition from {self.state.value} to {target.value}"
            )

    def set_state(self, new_state: PairState) -> None:
        with self._lock:
            self._transition(new_state)
            self.state = new_state

    def first_connection(self, android_uuid: str, nonce_android: bytes) -> None:
        with self._lock:
            self._transition(PairState.FIRST_CONNECTION)
            self.android_uuid = android_uuid
            self.nonce_android = nonce_android
            self.state = PairState.FIRST_CONNECTION

    def set_android_pubkey(self, pubkey_der: bytes) -> None:
        with self._lock:
            if self.state != PairState.FIRST_CONNECTION:
                raise state_conflict("Public key is not expected in this state")
            self.android_pubkey_der = pubkey_der

    def start_sas(self) -> str:
        with self._lock:
            self._transition(PairState.SAS_PENDING)
            if not (
                self.android_uuid
                and self.nonce_android
                and self.android_pubkey_der
            ):
                raise state_conflict("Pairing transcript is incomplete")
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
        with self._lock:
            self._transition(PairState.CONFIRMED_BOTH)
            self.state = PairState.CONFIRMED_BOTH

    def consume(self) -> None:
        with self._lock:
            self._transition(PairState.CONSUMED)
            self.state = PairState.CONSUMED

    def reject(self) -> None:
        with self._lock:
            self._transition(PairState.REJECTED)
            self.state = PairState.REJECTED

    def cancel(self) -> None:
        with self._lock:
            self._transition(PairState.CANCELLED)
            self.state = PairState.CANCELLED

    def fail(self) -> None:
        with self._lock:
            self._transition(PairState.FAILED)
            self.sas_attempts += 1
            if self.sas_attempts >= self.max_sas_attempts:
                self.state = PairState.FAILED

    def expire_if_needed(self) -> bool:
        with self._lock:
            if self.is_expired and not self.is_terminal:
                self.state = PairState.EXPIRED
            return self.state == PairState.EXPIRED


class PairingManager:
    """Thread-safe manager for bounded active pairing sessions."""

    def __init__(
        self,
        max_sessions: int = 5,
        *,
        expiry_seconds: int = 120,
        max_sas_attempts: int = 3,
        clock: Clock = None,
        rng: RandomSource = None,
    ):
        self._sessions: dict[str, PairingSession] = {}
        self._max = max_sessions
        self._expiry_seconds = expiry_seconds
        self._max_sas_attempts = max_sas_attempts
        self._clock = clock or SystemClock()
        self._rng = rng or SecureRandom()
        self._lock = threading.RLock()

    def create_session(
        self,
        desktop_uuid: str,
        desktop_pubkey_der: bytes,
        desktop_tls_spki_fp: str,
        clock: Clock = None,
        rng: RandomSource = None,
    ) -> PairingSession:
        with self._lock:
            self._remove_terminal()
            if len(self._sessions) >= self._max:
                raise DeviceLinkError(
                    429,
                    "PAIR_CAPACITY_EXCEEDED",
                    "Too many active pairing sessions",
                )
            session_rng = rng or self._rng
            session_id = session_rng.bytes(16).hex()
            while session_id in self._sessions:
                session_id = session_rng.bytes(16).hex()
            session = PairingSession(
                session_id,
                desktop_uuid,
                desktop_pubkey_der,
                desktop_tls_spki_fp,
                expiry_seconds=self._expiry_seconds,
                max_sas_attempts=self._max_sas_attempts,
                clock=clock or self._clock,
                rng=session_rng,
            )
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> PairingSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.expire_if_needed()
            return session

    def active_sessions(self) -> int:
        with self._lock:
            self._remove_terminal()
            return len(self._sessions)

    def _remove_terminal(self) -> None:
        for session in self._sessions.values():
            session.expire_if_needed()
        terminal_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if session.is_terminal
        ]
        for session_id in terminal_ids:
            del self._sessions[session_id]
