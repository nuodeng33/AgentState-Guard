"""Fail-closed physical-LAN selection and product-owned firewall rules."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from .errors import DeviceLinkError

_VIRTUAL_MARKERS = (
    "virtual",
    "hyper-v",
    "vethernet",
    "wsl",
    "docker",
    "vpn",
    "tailscale",
    "wireguard",
    "loopback",
    "bluetooth",
    "container",
)
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def is_rfc1918_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in _RFC1918_NETWORKS
    )


@dataclass(frozen=True)
class LanCandidate:
    interface: str
    description: str
    address: str
    prefix_length: int
    default_route: bool
    route_metric: int
    interface_metric: int
    private_profile: bool = True

    @property
    def combined_metric(self) -> int:
        return self.route_metric + self.interface_metric

    @property
    def subnet(self) -> str:
        return str(
            ipaddress.ip_network(f"{self.address}/{self.prefix_length}", strict=False)
        )


def select_physical_lan(candidates: tuple[LanCandidate, ...]) -> LanCandidate:
    eligible = []
    for candidate in candidates:
        label = f"{candidate.interface} {candidate.description}".lower()
        try:
            address = ipaddress.ip_address(candidate.address)
        except ValueError:
            continue
        if (
            not candidate.default_route
            or not candidate.private_profile
            or not isinstance(address, ipaddress.IPv4Address)
            or not is_rfc1918_ipv4(candidate.address)
            or address.is_loopback
            or address.is_link_local
            or any(marker in label for marker in _VIRTUAL_MARKERS)
            or not 1 <= candidate.prefix_length <= 30
        ):
            continue
        eligible.append(candidate)
    if not eligible:
        raise DeviceLinkError(
            503, "DEVICE_LAN_UNAVAILABLE", "No physical private LAN is available"
        )
    eligible.sort(key=lambda item: (item.combined_metric, item.interface, item.address))
    if len(eligible) > 1 and eligible[0].combined_metric == eligible[1].combined_metric:
        raise DeviceLinkError(
            409, "DEVICE_LAN_AMBIGUOUS", "Physical LAN selection is ambiguous"
        )
    return eligible[0]


class WindowsLanSelector:
    def __init__(self, runner=None) -> None:
        self._runner = runner or _powershell_json

    def select(self) -> LanCandidate:
        rows = self._runner(_LAN_SCRIPT)
        if isinstance(rows, dict):
            rows = [rows]
        candidates = tuple(
            LanCandidate(
                interface=str(row.get("InterfaceAlias", "")),
                description=str(row.get("InterfaceDescription", "")),
                address=str(row.get("IPAddress", "")),
                prefix_length=int(row.get("PrefixLength", 0)),
                default_route=bool(row.get("DefaultRoute", False)),
                route_metric=int(row.get("RouteMetric", 0)),
                interface_metric=int(row.get("InterfaceMetric", 0)),
                private_profile=bool(row.get("PrivateProfile", False)),
            )
            for row in rows
            if isinstance(row, dict)
        )
        return select_physical_lan(candidates)


class ScopedFirewall:
    TCP_RULE = "AgentState Guard Device Link TCP 8788"
    UDP_RULE = "AgentState Guard Device Link UDP 8788"

    def __init__(
        self,
        runner=None,
        *,
        elevation_runner=None,
        platform_name: str | None = None,
    ) -> None:
        platform_name = platform_name or os.name
        self._elevation = elevation_runner
        if runner is None and self._elevation is None and platform_name == "nt":
            from .windows_elevation import WindowsFirewallElevationRunner

            self._elevation = WindowsFirewallElevationRunner()
        self._runner = runner or _run_checked
        self._active_candidate: LanCandidate | None = None
        self._last_result: dict[str, object] = {
            "operation": "NONE",
            "status": "NOT_RUN",
            "reason_code": "DEVICE_FIREWALL_NOT_RUN",
            "scope_digest": None,
            "recorded_at": None,
        }

    def status(self) -> dict[str, object]:
        return dict(self._last_result)

    def apply(self, candidate: LanCandidate) -> None:
        if (
            not candidate.private_profile
            or not candidate.default_route
            or not is_rfc1918_ipv4(candidate.address)
            or not 1 <= candidate.prefix_length <= 30
        ):
            self._record("APPLY", "ERROR", "DEVICE_FIREWALL_SCOPE_INVALID", candidate)
            raise DeviceLinkError(
                409, "DEVICE_FIREWALL_SCOPE_INVALID", "Firewall scope is invalid"
            )
        if self._elevation is not None:
            result = self._elevation.apply(
                candidate.address,
                candidate.prefix_length,
            )
            if not result.ok:
                self._record("APPLY", "ERROR", result.reason_code, candidate)
                raise DeviceLinkError(
                    409
                    if result.reason_code
                    in {
                        "DEVICE_FIREWALL_SCOPE_INVALID",
                        "DEVICE_FIREWALL_ELEVATION_DECLINED",
                    }
                    else 503,
                    result.reason_code,
                    "Scoped firewall elevation failed",
                )
            self._active_candidate = candidate
            self._record("APPLY", "AVAILABLE", result.reason_code, candidate)
            return
        self.remove()
        try:
            for protocol, name in (("TCP", self.TCP_RULE), ("UDP", self.UDP_RULE)):
                self._runner(
                    [
                        "netsh.exe",
                        "advfirewall",
                        "firewall",
                        "add",
                        "rule",
                        f"name={name}",
                        "dir=in",
                        "action=allow",
                        f"protocol={protocol}",
                        "localport=8788",
                        f"localip={candidate.address}",
                        f"remoteip={candidate.subnet}",
                        "profile=private",
                        "enable=yes",
                    ]
                )
            self._active_candidate = candidate
            self._record("APPLY", "AVAILABLE", "DEVICE_FIREWALL_APPLIED", candidate)
        except (OSError, subprocess.SubprocessError) as error:
            self.remove()
            self._record("APPLY", "ERROR", "DEVICE_FIREWALL_FAILED", candidate)
            raise DeviceLinkError(
                503, "DEVICE_FIREWALL_FAILED", "Scoped firewall setup failed"
            ) from error

    def remove(self) -> None:
        if self._elevation is not None:
            candidate = self._active_candidate
            if candidate is None:
                return
            result = self._elevation.remove(
                candidate.address,
                candidate.prefix_length,
            )
            if not result.ok:
                self._record("REMOVE", "ERROR", result.reason_code, candidate)
                raise DeviceLinkError(
                    409
                    if result.reason_code == "DEVICE_FIREWALL_ELEVATION_DECLINED"
                    else 503,
                    result.reason_code,
                    "Scoped firewall removal failed",
                )
            self._active_candidate = None
            self._record("REMOVE", "AVAILABLE", result.reason_code, candidate)
            return
        candidate = self._active_candidate
        for name in (self.TCP_RULE, self.UDP_RULE):
            try:
                self._runner(
                    [
                        "netsh.exe",
                        "advfirewall",
                        "firewall",
                        "delete",
                        "rule",
                        f"name={name}",
                    ]
                )
            except (OSError, subprocess.SubprocessError):
                continue
        self._active_candidate = None
        if candidate is not None:
            self._record("REMOVE", "AVAILABLE", "DEVICE_FIREWALL_REMOVED", candidate)

    def _record(
        self,
        operation: str,
        status: str,
        reason_code: str,
        candidate: LanCandidate,
    ) -> None:
        scope = f"{candidate.address}/{candidate.prefix_length}:{candidate.subnet}:8788"
        self._last_result = {
            "operation": operation,
            "status": status,
            "reason_code": reason_code,
            "scope_digest": hashlib.sha256(scope.encode()).hexdigest(),
            "recorded_at": datetime.now(UTC).isoformat(),
        }


def _run_checked(args: list[str]) -> None:
    subprocess.run(
        args,
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _powershell_json(script: str):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout or "[]")


_LAN_SCRIPT = r"""
$routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop
$ips = Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction Stop
$ipInterfaces = Get-NetIPInterface -AddressFamily IPv4 -ErrorAction Stop
$profiles = Get-NetConnectionProfile -ErrorAction Stop
$adapters = Get-NetAdapter -Physical -ErrorAction Stop | Where-Object Status -eq 'Up'
@($adapters | ForEach-Object {
  $adapter = $_
  $address = $ips | Where-Object InterfaceIndex -eq $adapter.ifIndex | Select-Object -First 1
  $route = $routes | Where-Object InterfaceIndex -eq $adapter.ifIndex | Sort-Object RouteMetric | Select-Object -First 1
  $ipInterface = $ipInterfaces | Where-Object InterfaceIndex -eq $adapter.ifIndex | Select-Object -First 1
  $profile = $profiles | Where-Object InterfaceIndex -eq $adapter.ifIndex | Select-Object -First 1
  if ($null -ne $address -and $null -ne $route) {
    [pscustomobject]@{
      InterfaceAlias=$adapter.Name; InterfaceDescription=$adapter.InterfaceDescription
      IPAddress=$address.IPAddress; PrefixLength=$address.PrefixLength; DefaultRoute=$true
      RouteMetric=$route.RouteMetric; InterfaceMetric=$ipInterface.InterfaceMetric
      PrivateProfile=($profile.NetworkCategory -eq 'Private')
    }
  }
}) | ConvertTo-Json -Compress
"""
