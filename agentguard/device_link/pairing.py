"""ASDL/1 Pairing State Machine."""

import time as _time
from enum import Enum
from typing import Dict, Optional, Tuple

from .crypto import (
    random_bytes, random_session_id, derive_sas, build_pairing_transcript,
    format_sas, spki_fingerprint,
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


TERMINAL_STATES = {PairState.CONSUMED, PairState.EXPIRED, PairState.REJECTED,
                   PairState.FAILED, PairState.CANCELLED}


class PairingSession:
    """One pairing session — 120s TTL, single-use."""

    def __init__(
        self,
        session_id: str,
        desktop_uuid: str,
        desktop_pubkey_der: bytes,
        desktop_tls_spki_fp: str,
        expiry_seconds: int = 120,
        max_sas_attempts: int = 3,
    ):
        self.session_id = session_id
        self.desktop_uuid = desktop_uuid
        self.desktop_pubkey_der = desktop_pubkey_der
        self.desktop_tls_spki_fp = desktop_tls_spki_fp
        self.pairing_secret = random_bytes(32)
        self.nonce_desktop = random_bytes(32)
        self.nonce_android: Optional[bytes] = None
        self.android_uuid: Optional[str] = None
        self.android_pubkey_der: Optional[bytes] = None
        self.expiry = int(_time.time()) + expiry_seconds
        self.state = PairState.CREATED
        self.sas_attempts = 0
        self.max_sas_attempts = max_sas_attempts

    @property
    def is_expired(self) -> bool:
        return int(_time.time()) >= self.expiry

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def checkpoint(self) -> None:
        if self.is_expired and not self.is_terminal:
            self.state = PairState.EXPIRED

    def first_connection(self, android_uuid: str, nonce_android: bytes) -> None:
        self.checkpoint()
        if self.is_terminal or self.state != PairState.CREATED:
            raise ValueError(f"Invalid state: {self.state}")
        self.android_uuid = android_uuid
        self.nonce_android = nonce_android
        self.state = PairState.FIRST_CONNECTION

    def start_sas(self) -> str:
        """Compute and return formatted SAS. Both sides must match."""
        self.checkpoint()
        if self.is_terminal or self.state != PairState.FIRST_CONNECTION:
            raise ValueError(f"Invalid state for SAS: {self.state}")
        assert self.android_uuid and self.nonce_android is not None
        assert self.android_pubkey_der is not None

        transcript = build_pairing_transcript(
            1,  # protocol_version
            self.session_id,
            self.desktop_uuid,
            self.android_uuid,
            self.desktop_pubkey_der,
            self.android_pubkey_der,
            self.desktop_tls_spki_fp,
            self.nonce_desktop,
            self.nonce_android,
            self.expiry,
        )
        sas = derive_sas(self.pairing_secret, transcript)
        self.state = PairState.SAS_PENDING
        return format_sas(sas)

    def confirm(self) -> None:
        self.checkpoint()
        if self.is_terminal or self.state != PairState.SAS_PENDING:
            raise ValueError(f"Invalid state for confirm: {self.state}")
        self.state = PairState.CONFIRMED_BOTH

    def consume(self) -> None:
        self.checkpoint()
        if self.is_terminal or self.state != PairState.CONFIRMED_BOTH:
            raise ValueError(f"Invalid state for consume: {self.state}")
        self.state = PairState.CONSUMED

    def reject(self) -> None:
        self.checkpoint()
        if self.is_terminal:
            return
        self.state = PairState.REJECTED

    def cancel(self) -> None:
        self.checkpoint()
        if self.is_terminal:
            return
        self.state = PairState.CANCELLED

    def fail(self) -> None:
        self.checkpoint()
        self.sas_attempts += 1
        if self.sas_attempts >= self.max_sas_attempts:
            self.state = PairState.FAILED

    def set_android_pubkey(self, pubkey_der: bytes) -> None:
        self.android_pubkey_der = pubkey_der


class PairingManager:
    """Manages active pairing sessions."""

    def __init__(self, max_sessions: int = 5):
        self._sessions: Dict[str, PairingSession] = {}
        self._max = max_sessions

    def create_session(
        self, desktop_uuid: str, desktop_pubkey_der: bytes,
        desktop_tls_spki_fp: str,
    ) -> PairingSession:
        # Purge expired terminals first
        to_del = [
            sid for sid, s in self._sessions.items()
            if s.is_terminal and s.is_expired
        ]
        for sid in to_del:
            del self._sessions[sid]

        # Purge active expired
        for s in list(self._sessions.values()):
            s.checkpoint()

        if len(self._sessions) >= self._max:
            # Remove oldest expired/terminal
            for sid in list(self._sessions.keys()):
                if self._sessions[sid].is_terminal:
                    del self._sessions[sid]
                    break

        session_id = random_session_id()
        session = PairingSession(
            session_id, desktop_uuid, desktop_pubkey_der, desktop_tls_spki_fp,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[PairingSession]:
        session = self._sessions.get(session_id)
        if session:
            session.checkpoint()
        return session

    def active_sessions(self) -> int:
        for s in list(self._sessions.values()):
            s.checkpoint()
        to_del = [sid for sid, s in self._sessions.items() if s.is_terminal]
        for sid in to_del:
            del self._sessions[sid]
        return len(self._sessions)
