"""Physical-LAN selection and scoped firewall contract tests."""

from __future__ import annotations

import pytest

from agentguard.device_link.errors import DeviceLinkError
from agentguard.device_link.network import (
    LanCandidate,
    ScopedFirewall,
    select_physical_lan,
)


def test_physical_private_default_route_wins_by_combined_metric():
    selected = select_physical_lan(
        (
            LanCandidate("Ethernet", "Intel I219", "192.168.50.8", 24, True, 25, 5),
            LanCandidate("Wi-Fi", "Intel Wi-Fi", "10.0.0.8", 24, True, 35, 5),
            LanCandidate("Tailscale", "Tailscale Tunnel", "100.64.0.1", 32, True, 1, 1),
            LanCandidate(
                "WSL", "Hyper-V Virtual Ethernet", "172.20.0.1", 20, True, 1, 1
            ),
        )
    )
    assert selected.address == "192.168.50.8"
    assert selected.subnet == "192.168.50.0/24"


def test_ambiguous_or_non_private_selection_fails_closed():
    with pytest.raises(DeviceLinkError) as ambiguous:
        select_physical_lan(
            (
                LanCandidate("Ethernet 1", "Intel", "192.168.1.2", 24, True, 10, 10),
                LanCandidate("Ethernet 2", "Realtek", "192.168.2.2", 24, True, 10, 10),
            )
        )
    assert ambiguous.value.code == "DEVICE_LAN_AMBIGUOUS"

    with pytest.raises(DeviceLinkError) as public_profile:
        select_physical_lan(
            (
                LanCandidate(
                    "Ethernet",
                    "Intel",
                    "192.168.1.2",
                    24,
                    True,
                    10,
                    10,
                    private_profile=False,
                ),
            )
        )
    assert public_profile.value.code == "DEVICE_LAN_UNAVAILABLE"

    with pytest.raises(DeviceLinkError) as non_rfc1918:
        select_physical_lan(
            (LanCandidate("Ethernet", "Intel", "198.18.0.2", 24, True, 10, 10),)
        )
    assert non_rfc1918.value.code == "DEVICE_LAN_UNAVAILABLE"


def test_firewall_mutates_only_exact_owned_tcp_udp_rules():
    calls = []
    firewall = ScopedFirewall(runner=lambda args: calls.append(tuple(args)))
    candidate = LanCandidate("Ethernet", "Intel", "192.168.1.8", 24, True, 10, 5)

    firewall.apply(candidate)
    firewall.remove()

    rendered = "\n".join(" ".join(call) for call in calls)
    assert "AgentState Guard Device Link TCP 8788" in rendered
    assert "AgentState Guard Device Link UDP 8788" in rendered
    assert "remoteip=192.168.1.0/24" in rendered
    assert "localip=192.168.1.8" in rendered
    assert "0.0.0.0" not in rendered
