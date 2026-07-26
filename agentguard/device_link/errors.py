"""Typed Device Link errors shared by the gateway and HTTP adapter."""

from __future__ import annotations


class DeviceLinkError(ValueError):
    """A safe, externally mappable Device Link failure."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def invalid_request(message: str = "Invalid Device Link request") -> DeviceLinkError:
    return DeviceLinkError(400, "DEVICE_INVALID_REQUEST", message)


def state_conflict(message: str = "Pairing state conflict") -> DeviceLinkError:
    return DeviceLinkError(409, "PAIR_STATE_CONFLICT", message)
