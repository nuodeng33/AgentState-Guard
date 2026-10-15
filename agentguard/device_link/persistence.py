"""SQLite-backed minimal Device Link binding metadata."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from agentguard.storage.db import StateDB

from .crypto import spki_fingerprint


class DeviceBindingStore:
    def __init__(self, database: StateDB, *, single_device: bool = True) -> None:
        if database._conn is None:
            raise RuntimeError("DEVICE_BINDING_DATABASE_UNAVAILABLE")
        self._db_path = database.db_path
        self._single_device = single_device

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def add(
        self,
        device_uuid: str,
        pubkey_der: bytes,
        display_name: str,
        permissions: list[str],
        protocol_version: int,
    ) -> None:
        if self.get(device_uuid) is not None:
            raise ValueError("Device UUID already bound")
        if self._single_device and self.count():
            raise ValueError("Maximum devices already bound")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO device_link_bindings
                   (device_uuid, public_key_der, fingerprint, display_name,
                    permissions, protocol_version, created_at, last_seen_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                   ON CONFLICT(device_uuid) DO UPDATE SET
                     public_key_der=excluded.public_key_der,
                     fingerprint=excluded.fingerprint,
                     display_name=excluded.display_name,
                     permissions=excluded.permissions,
                     protocol_version=excluded.protocol_version,
                     created_at=excluded.created_at,
                     last_seen_at=NULL,
                     revoked_at=NULL
                   WHERE device_link_bindings.revoked_at IS NOT NULL""",
                (
                    device_uuid,
                    pubkey_der,
                    spki_fingerprint(pubkey_der),
                    display_name,
                    ",".join(sorted(set(permissions))),
                    protocol_version,
                    now,
                ),
            )

    def get(self, device_uuid: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT device_uuid, public_key_der, fingerprint, display_name,
                          permissions, protocol_version, created_at, last_seen_at
                   FROM device_link_bindings
                   WHERE device_uuid = ? AND revoked_at IS NULL""",
                (device_uuid,),
            ).fetchone()
        return _row(row) if row else None

    def list(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT device_uuid, public_key_der, fingerprint, display_name,
                          permissions, protocol_version, created_at, last_seen_at
                   FROM device_link_bindings WHERE revoked_at IS NULL
                   ORDER BY created_at"""
            ).fetchall()
        return [_public_row(row) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM device_link_bindings WHERE revoked_at IS NULL"
                ).fetchone()[0]
            )

    def update_last_seen(self, device_uuid: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE device_link_bindings SET last_seen_at = ?
                   WHERE device_uuid = ? AND revoked_at IS NULL""",
                (datetime.now(UTC).isoformat(), device_uuid),
            )

    def remove(self, device_uuid: str) -> bool:
        return self.revoke(device_uuid)

    def revoke(self, device_uuid: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE device_link_bindings SET revoked_at = ?
                   WHERE device_uuid = ? AND revoked_at IS NULL""",
                (datetime.now(UTC).isoformat(), device_uuid),
            )
            return cursor.rowcount == 1


def _row(row) -> dict:
    return {
        "uuid": row[0],
        "pubkey_der_hex": bytes(row[1]).hex(),
        "fingerprint": row[2],
        "display_name": row[3],
        "permissions": row[4].split(",") if row[4] else [],
        "protocol_version": row[5],
        "created_at": row[6],
        "last_seen": row[7],
    }


def _public_row(row) -> dict:
    value = _row(row)
    value.pop("pubkey_der_hex")
    value.pop("permissions")
    value.pop("protocol_version")
    value.pop("created_at")
    return value
