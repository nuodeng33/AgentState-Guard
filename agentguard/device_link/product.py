"""Single product composition for durable Device Link state and the 8788 app."""

from __future__ import annotations

from pathlib import Path

from agentguard.storage.db import StateDB

from .api import create_device_link_app
from .gateway import DeviceLinkGateway, GatewayConfig
from .identity import DesktopIdentityStore
from .lifecycle import DeviceLinkController, UvicornDeviceLinkListener
from .network import ScopedFirewall, WindowsLanSelector
from .persistence import DeviceBindingStore


class UnavailableDeviceLinkController:
    gateway = None

    def __init__(self, reason_code: str = "DEVICE_LINK_AUTHORITY_UNAVAILABLE") -> None:
        self.reason_code = reason_code

    def status(self) -> dict[str, object]:
        return {
            "schema_version": "device-link-lifecycle-1",
            "enabled": False,
            "status": "DEGRADED",
            "endpoint": None,
            "address": None,
            "subnet": None,
            "reason_code": self.reason_code,
        }

    def product_status(self) -> dict[str, object]:
        return {
            **self.status(),
            "desktop_uuid": None,
            "desktop_signing_fingerprint": None,
            "tls_spki_fingerprint": None,
            "bound_devices": [],
            "active_pair_sessions": 0,
        }

    def enable(self):
        from .errors import DeviceLinkError

        raise DeviceLinkError(
            503, self.reason_code, "Device Link authority is unavailable"
        )

    refresh_network = enable
    create_pairing_invitation = enable

    def disable(self):
        return self.status()


def build_device_link_product(
    *,
    state_db_path: Path,
    snapshots_dir: Path,
    state_dir: Path,
    advisory_getter=None,
) -> DeviceLinkController:
    database = StateDB(state_db_path)
    database.connect()
    registry = DeviceBindingStore(database, single_device=True)
    identity = DesktopIdentityStore(
        state_dir / "device-link" / "desktop-identity.json"
    ).load_or_create()
    database.close()
    gateway = DeviceLinkGateway(
        identity.desktop_uuid,
        identity.signing_public_key_der,
        identity.signing_private_key_pem,
        desktop_tls_spki_fp=identity.tls_spki_fingerprint,
        config=GatewayConfig(single_device=True),
        device_registry=registry,
        single_device=True,
    )
    device_app = create_device_link_app(
        gateway=gateway,
        state_db_path=state_db_path,
        snapshots_dir=snapshots_dir,
        advisory_getter=advisory_getter,
    )
    listener = UvicornDeviceLinkListener(
        device_app,
        identity,
        state_dir / "device-link" / "runtime",
    )
    return DeviceLinkController(
        WindowsLanSelector(),
        ScopedFirewall(),
        listener,
        gateway=gateway,
        identity=identity,
    )
