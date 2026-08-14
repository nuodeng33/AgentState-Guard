"""Durable Desktop Device Link identity with OS-bound private material."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .crypto import generate_ecdsa_p256_keypair, public_key_to_der, spki_fingerprint


@dataclass(frozen=True)
class DesktopIdentity:
    desktop_uuid: str
    signing_private_key_pem: bytes
    signing_public_key_der: bytes
    tls_private_key_pem: bytes
    tls_public_key_der: bytes

    @property
    def signing_fingerprint(self) -> str:
        return spki_fingerprint(self.signing_public_key_der)

    @property
    def tls_spki_fingerprint(self) -> str:
        return spki_fingerprint(self.tls_public_key_der)


class DesktopIdentityStore:
    """Persist identity once; DPAPI protects keys on Windows."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self) -> DesktopIdentity:
        if self.path.exists() or self.path.is_symlink():
            if self.path.is_symlink() or not self.path.is_file():
                raise OSError("DEVICE_IDENTITY_PATH_UNSAFE")
            return self._decode(json.loads(self.path.read_text(encoding="utf-8")))
        signing_private, signing_public = generate_ecdsa_p256_keypair()
        tls_private, tls_public = generate_ecdsa_p256_keypair()
        payload = {
            "schema_version": 1,
            "desktop_uuid": str(uuid4()),
            "protection": "DPAPI_CURRENT_USER" if os.name == "nt" else "MODE_0600_DEV",
            "signing_private": base64.b64encode(_protect(signing_private)).decode(
                "ascii"
            ),
            "tls_private": base64.b64encode(_protect(tls_private)).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise OSError("DEVICE_IDENTITY_DIRECTORY_UNSAFE")
        temporary = self.path.with_suffix(".tmp")
        if temporary.exists() or temporary.is_symlink():
            raise OSError("DEVICE_IDENTITY_TEMP_UNSAFE")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(payload, output, sort_keys=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return DesktopIdentity(
            desktop_uuid=payload["desktop_uuid"],
            signing_private_key_pem=signing_private,
            signing_public_key_der=public_key_to_der(signing_public),
            tls_private_key_pem=tls_private,
            tls_public_key_der=public_key_to_der(tls_public),
        )

    def _decode(self, payload: object) -> DesktopIdentity:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("DEVICE_IDENTITY_INVALID")
        desktop_uuid = payload.get("desktop_uuid")
        signing = payload.get("signing_private")
        tls = payload.get("tls_private")
        if not all(
            isinstance(value, str) and value for value in (desktop_uuid, signing, tls)
        ):
            raise ValueError("DEVICE_IDENTITY_INVALID")
        signing_private = _unprotect(base64.b64decode(signing, validate=True))
        tls_private = _unprotect(base64.b64decode(tls, validate=True))
        return DesktopIdentity(
            desktop_uuid=desktop_uuid,
            signing_private_key_pem=signing_private,
            signing_public_key_der=_private_public_der(signing_private),
            tls_private_key_pem=tls_private,
            tls_public_key_der=_private_public_der(tls_private),
        )


def _private_public_der(private_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization

    private = serialization.load_pem_private_key(private_pem, password=None)
    return private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    ), buffer


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    source, keepalive = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del keepalive


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    source, keepalive = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del keepalive
