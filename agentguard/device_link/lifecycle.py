"""Disabled-by-default lifecycle for the independent Device Link listener."""

from __future__ import annotations

import json
import os
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from .errors import DeviceLinkError
from .network import is_rfc1918_ipv4


class DeviceLinkController:
    def __init__(
        self, selector, firewall, listener, *, gateway=None, identity=None
    ) -> None:
        self._selector = selector
        self._firewall = firewall
        self._listener = listener
        self._candidate = None
        self._enabled = False
        self._last_reason_code: str | None = None
        self.gateway = gateway
        self.identity = identity

    def status(self) -> dict[str, object]:
        result = {
            "schema_version": "device-link-lifecycle-1",
            "enabled": self._enabled,
            "status": (
                "ENABLED"
                if self._enabled
                else "DEGRADED"
                if self._last_reason_code is not None
                else "DISABLED"
            ),
            "endpoint": (
                f"https://{self._candidate.address}:8788"
                if self._enabled and self._candidate is not None
                else None
            ),
            "address": self._candidate.address
            if self._enabled and self._candidate
            else None,
            "subnet": self._candidate.subnet
            if self._enabled and self._candidate
            else None,
            "reason_code": (
                "DEVICE_LINK_ENABLED"
                if self._enabled
                else self._last_reason_code or "DEVICE_LINK_DISABLED"
            ),
        }
        firewall_status = getattr(self._firewall, "status", None)
        result["firewall"] = (
            firewall_status()
            if callable(firewall_status)
            else {
                "operation": "UNKNOWN",
                "status": "UNKNOWN",
                "reason_code": "DEVICE_FIREWALL_STATUS_UNAVAILABLE",
                "scope_digest": None,
                "recorded_at": None,
            }
        )
        return result

    def product_status(self) -> dict[str, object]:
        lifecycle = self.status()
        devices = self.gateway.list_bound_devices() if self.gateway else []
        return {
            **lifecycle,
            "desktop_uuid": self.gateway.desktop_uuid if self.gateway else None,
            "desktop_signing_fingerprint": (
                self.identity.signing_fingerprint if self.identity else None
            ),
            "tls_spki_fingerprint": (
                self.identity.tls_spki_fingerprint if self.identity else None
            ),
            "bound_devices": devices,
            "active_pair_sessions": (
                self.gateway.pairing_mgr.active_sessions() if self.gateway else 0
            ),
        }

    def create_pairing_invitation(self) -> dict[str, object]:
        if not self._enabled or self._candidate is None or self.gateway is None:
            raise DeviceLinkError(
                409, "DEVICE_LINK_DISABLED", "Enable Device Link before pairing"
            )
        return self.gateway.create_pairing_invitation(self._candidate.address, 8788)

    def enable(self) -> dict[str, object]:
        if self._enabled:
            return self.status()
        try:
            candidate = self._selector.select()
        except DeviceLinkError:
            raise
        except Exception as error:
            raise DeviceLinkError(
                503, "DEVICE_LAN_OBSERVATION_FAILED", "Physical LAN observation failed"
            ) from error
        try:
            self._firewall.apply(candidate)
            self._listener.start(candidate.address, 8788)
        except DeviceLinkError as error:
            self._last_reason_code = error.code
            try:
                self._listener.stop()
            finally:
                try:
                    self._firewall.remove()
                except DeviceLinkError as cleanup_error:
                    self._last_reason_code = cleanup_error.code
                    raise cleanup_error from error
            raise
        except Exception as error:
            failure = DeviceLinkError(
                503, "DEVICE_LINK_ENABLE_FAILED", "Device Link enable failed"
            )
            self._last_reason_code = failure.code
            try:
                self._listener.stop()
            finally:
                try:
                    self._firewall.remove()
                except DeviceLinkError as cleanup_error:
                    self._last_reason_code = cleanup_error.code
                    raise cleanup_error from error
            raise failure from error
        self._candidate = candidate
        self._enabled = True
        self._last_reason_code = None
        return self.status()

    def refresh_network(self) -> dict[str, object]:
        if not self._enabled:
            return self.status()
        try:
            candidate = self._selector.select()
        except DeviceLinkError:
            self.disable()
            raise
        except Exception as error:
            self.disable()
            raise DeviceLinkError(
                503, "DEVICE_LAN_OBSERVATION_FAILED", "Physical LAN observation failed"
            ) from error
        if (
            self._candidate
            and candidate.address == self._candidate.address
            and candidate.subnet == self._candidate.subnet
        ):
            return self.status()
        try:
            self._listener.stop()
            self._firewall.remove()
        except DeviceLinkError as error:
            self._last_reason_code = error.code
            raise
        finally:
            self._enabled = False
            self._candidate = None
        return self.enable_with(candidate)

    def enable_with(self, candidate) -> dict[str, object]:
        try:
            self._firewall.apply(candidate)
            self._listener.start(candidate.address, 8788)
        except DeviceLinkError as error:
            self._last_reason_code = error.code
            try:
                self._listener.stop()
            finally:
                try:
                    self._firewall.remove()
                except DeviceLinkError as cleanup_error:
                    self._last_reason_code = cleanup_error.code
                    raise cleanup_error from error
            raise
        except Exception as error:
            failure = DeviceLinkError(
                503, "DEVICE_LINK_REBIND_FAILED", "Device Link rebind failed"
            )
            self._last_reason_code = failure.code
            try:
                self._listener.stop()
            finally:
                try:
                    self._firewall.remove()
                except DeviceLinkError as cleanup_error:
                    self._last_reason_code = cleanup_error.code
                    raise cleanup_error from error
            raise failure from error
        self._candidate = candidate
        self._enabled = True
        self._last_reason_code = None
        return self.status()

    def disable(self) -> dict[str, object]:
        firewall_error: DeviceLinkError | None = None
        try:
            self._listener.stop()
        finally:
            try:
                self._firewall.remove()
            except DeviceLinkError as error:
                firewall_error = error
                self._last_reason_code = error.code
            finally:
                if self.gateway is not None:
                    self.gateway.invalidate_transient_authorizations()
                self._candidate = None
                self._enabled = False
        if firewall_error is not None:
            raise firewall_error
        self._last_reason_code = None
        return self.status()


class UvicornDeviceLinkListener:
    """Own exactly one TLS 1.3 uvicorn listener and its ephemeral cert files."""

    def __init__(self, app, identity, runtime_dir: Path) -> None:
        self._app = app
        self._identity = identity
        self._runtime_dir = runtime_dir
        self._server = None
        self._thread = None
        self._rediscovery = BoundRediscoveryResponder(identity)

    def start(self, host: str, port: int) -> None:
        import uvicorn

        if not is_rfc1918_ipv4(host) or port != 8788:
            raise DeviceLinkError(
                409, "DEVICE_BIND_TARGET_INVALID", "Invalid Device Link bind target"
            )
        cert_path, key_path = self._materialize_tls(host)
        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
            ssl_version=ssl.PROTOCOL_TLS_SERVER,
        )
        config.load()
        config.ssl.minimum_version = ssl.TLSVersion.TLSv1_3
        config.ssl.maximum_version = ssl.TLSVersion.TLSv1_3
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5
        while (
            not self._server.started
            and self._thread.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if not self._server.started:
            self.stop()
            raise DeviceLinkError(
                503, "DEVICE_LISTENER_FAILED", "TLS listener did not start"
            )
        try:
            self._rediscovery.start(host, port)
        except OSError as error:
            self.stop()
            raise DeviceLinkError(
                503, "DEVICE_REDISCOVERY_FAILED", "Rediscovery did not start"
            ) from error

    def stop(self) -> None:
        self._rediscovery.stop()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        for name in ("listener-cert.pem", "listener-key.pem"):
            (self._runtime_dir / name).unlink(missing_ok=True)

    def _materialize_tls(self, host: str) -> tuple[Path, Path]:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import NameOID

        private = serialization.load_pem_private_key(
            self._identity.tls_private_key_pem, password=None
        )
        now = datetime.now(UTC)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "AgentState Guard Device Link")]
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ip_address(host))]),
                critical=False,
            )
            .sign(private, hashes.SHA256())
        )
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        if self._runtime_dir.is_symlink():
            raise OSError("DEVICE_TLS_RUNTIME_UNSAFE")
        cert_path = self._runtime_dir / "listener-cert.pem"
        key_path = self._runtime_dir / "listener-key.pem"
        if (
            cert_path.exists()
            or cert_path.is_symlink()
            or key_path.exists()
            or key_path.is_symlink()
        ):
            raise OSError("DEVICE_TLS_MATERIAL_UNSAFE")
        with cert_path.open("xb") as output:
            output.write(certificate.public_bytes(serialization.Encoding.PEM))
            output.flush()
            os.fsync(output.fileno())
        with key_path.open("xb") as output:
            output.write(self._identity.tls_private_key_pem)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(cert_path, 0o600)
        os.chmod(key_path, 0o600)
        return cert_path, key_path


class BoundRediscoveryResponder:
    """Reply only when a datagram names the already-bound Desktop UUID."""

    def __init__(self, identity) -> None:
        self._identity = identity
        self._socket = None
        self._thread = None
        self._running = False
        self._host = None
        self._port = 8788

    def response(self, payload: bytes) -> bytes | None:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, dict)
            or value.get("protocol") != "ASDL_DISCOVERY_1"
            or value.get("desktop_uuid") != self._identity.desktop_uuid
        ):
            return None
        return json.dumps(
            {
                "protocol": "ASDL_DISCOVERY_1",
                "desktop_uuid": self._identity.desktop_uuid,
                "port": self._port,
                "tls_spki_fingerprint": self._identity.tls_spki_fingerprint,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def start(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(0.5)
        self._socket.bind((host, port))
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._socket = None
        self._thread = None

    def _serve(self) -> None:
        while self._running and self._socket is not None:
            try:
                payload, peer = self._socket.recvfrom(1024)
                response = self.response(payload)
                if response is not None:
                    self._socket.sendto(response, peer)
            except TimeoutError:
                continue
            except OSError:
                break
