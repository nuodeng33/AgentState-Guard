"""Strict Pydantic request contracts for the Device Link HTTP boundary."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LOWER_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
ProtocolVersion = Annotated[StrictInt, Field(ge=1, le=1)]


def validate_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier must be 1-128 safe ASCII characters")
    return value


def validate_session_id(value: str) -> str:
    if not _LOWER_HEX_32_RE.fullmatch(value):
        raise ValueError("session ID must be 32 lowercase hexadecimal characters")
    return value


def _validate_hex(value: str, *, exact_bytes: int | None = None,
                  max_bytes: int | None = None, field_name: str = "value") -> str:
    if len(value) % 2 or not value or not _HEX_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be non-empty hexadecimal")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be hexadecimal") from exc
    if exact_bytes is not None and len(decoded) != exact_bytes:
        raise ValueError(f"{field_name} must be exactly {exact_bytes} bytes")
    if max_bytes is not None and len(decoded) > max_bytes:
        raise ValueError(f"{field_name} must be at most {max_bytes} bytes")
    return value.lower()


class StrictDeviceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyRequest(StrictDeviceModel):
    pass


class PairConnectRequest(StrictDeviceModel):
    android_uuid: str
    nonce: str

    @field_validator("android_uuid")
    @classmethod
    def _device_id(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("nonce")
    @classmethod
    def _nonce(cls, value: str) -> str:
        return _validate_hex(value, exact_bytes=32, field_name="nonce")


class PairSasRequest(StrictDeviceModel):
    android_pubkey_der_hex: str

    @field_validator("android_pubkey_der_hex")
    @classmethod
    def _public_key(cls, value: str) -> str:
        return _validate_hex(
            value,
            max_bytes=512,
            field_name="public key DER",
        )


class PairConfirmRequest(StrictDeviceModel):
    confirm: StrictBool


class PairCompleteRequest(StrictDeviceModel):
    android_uuid: str
    android_pubkey_der_hex: str
    display_name: Annotated[str, Field(min_length=1, max_length=128)]
    protocol_version: ProtocolVersion

    @field_validator("android_uuid")
    @classmethod
    def _device_id(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("android_pubkey_der_hex")
    @classmethod
    def _public_key(cls, value: str) -> str:
        return _validate_hex(
            value,
            max_bytes=512,
            field_name="public key DER",
        )

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display name must not be blank")
        return normalized


class AuthChallengeRequest(StrictDeviceModel):
    device_uuid: str
    protocol_version: ProtocolVersion

    @field_validator("device_uuid")
    @classmethod
    def _device_id(cls, value: str) -> str:
        return validate_identifier(value)


class AuthResponseRequest(StrictDeviceModel):
    device_uuid: str
    challenge_id: str
    signature: str
    protocol_version: ProtocolVersion

    @field_validator("device_uuid")
    @classmethod
    def _device_id(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("challenge_id")
    @classmethod
    def _challenge_id(cls, value: str) -> str:
        if not _LOWER_HEX_32_RE.fullmatch(value):
            raise ValueError(
                "challenge ID must be 32 lowercase hexadecimal characters"
            )
        return value

    @field_validator("signature")
    @classmethod
    def _signature(cls, value: str) -> str:
        return _validate_hex(value, max_bytes=512, field_name="signature")
