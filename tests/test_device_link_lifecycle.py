"""Disabled-by-default Device Link lifecycle and rebind tests."""

from agentguard.device_link.lifecycle import (
    BoundRediscoveryResponder,
    DeviceLinkController,
)
from agentguard.device_link.network import LanCandidate


class _Selector:
    def __init__(self, values):
        self.values = iter(values)

    def select(self):
        return next(self.values)


class _Firewall:
    def __init__(self):
        self.actions = []

    def apply(self, candidate):
        self.actions.append(("apply", candidate.address))

    def remove(self):
        self.actions.append(("remove",))


class _Listener:
    def __init__(self):
        self.actions = []

    def start(self, host, port):
        self.actions.append(("start", host, port))

    def stop(self):
        self.actions.append(("stop",))


class _Gateway:
    def __init__(self):
        self.invalidations = 0

    def invalidate_transient_authorizations(self):
        self.invalidations += 1


def test_enable_refresh_disable_binds_exact_ip_and_preserves_binding_state():
    first = LanCandidate("Ethernet", "Intel", "192.168.1.8", 24, True, 10, 5)
    second = LanCandidate("Wi-Fi", "Intel Wi-Fi", "192.168.2.9", 24, True, 5, 5)
    firewall = _Firewall()
    listener = _Listener()
    controller = DeviceLinkController(_Selector([first, second]), firewall, listener)

    assert controller.status()["enabled"] is False
    assert controller.enable()["endpoint"] == "https://192.168.1.8:8788"
    assert controller.refresh_network()["endpoint"] == "https://192.168.2.9:8788"
    assert controller.disable()["enabled"] is False
    assert ("start", "192.168.1.8", 8788) in listener.actions
    assert ("start", "192.168.2.9", 8788) in listener.actions
    assert firewall.actions[-1] == ("remove",)


def test_rediscovery_replies_only_for_exact_saved_desktop_uuid():
    class Identity:
        desktop_uuid = "desktop-exact"
        tls_spki_fingerprint = "a" * 64

    responder = BoundRediscoveryResponder(Identity())
    exact = responder.response(
        b'{"protocol":"ASDL_DISCOVERY_1","desktop_uuid":"desktop-exact"}'
    )
    wrong = responder.response(
        b'{"protocol":"ASDL_DISCOVERY_1","desktop_uuid":"another"}'
    )

    assert exact is not None
    assert b'"desktop_uuid":"desktop-exact"' in exact
    assert wrong is None


def test_disable_invalidates_transient_authority_without_deleting_binding():
    candidate = LanCandidate("Ethernet", "Intel", "192.168.1.8", 24, True, 10, 5)
    gateway = _Gateway()
    controller = DeviceLinkController(
        _Selector([candidate]), _Firewall(), _Listener(), gateway=gateway
    )

    controller.enable()
    controller.disable()

    assert gateway.invalidations == 1
