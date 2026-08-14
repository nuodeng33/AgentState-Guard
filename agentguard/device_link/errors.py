"""Stable fail-closed errors for the Device Link product boundary."""


class DeviceLinkError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def invalid_request(message: str = "Invalid Device Link request") -> DeviceLinkError:
    return DeviceLinkError(400, "DEVICE_INVALID_REQUEST", message)


def state_conflict(message: str = "Device Link state conflict") -> DeviceLinkError:
    return DeviceLinkError(409, "DEVICE_STATE_CONFLICT", message)
