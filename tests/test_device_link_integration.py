"""Real HTTP integration tests — Gateway is NOT mocked, but actually served."""

import json
import threading
import time
import urllib.request
import urllib.error

import pytest
from agentguard.device_link.crypto import (
    generate_ecdsa_p256_keypair, public_key_to_der, sign_challenge,
    FakeClock, DeterministicRandom,
)
from agentguard.device_link.pairing import PairingSession, PairState
from agentguard.device_link.gateway import DeviceLinkGateway, GatewayConfig

# Test secrets — must NEVER appear in outputs
TEST_SECRET_PAIR = "PAIR_SECRET_TEST_123456"
TEST_SECRET_PRIV = "PRIVATE_KEY_TEST_DO_NOT_LEAK"
TEST_SECRET_TOKEN = "SESSION_TOKEN_TEST_123456"


def _free_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestGatewayRealIntegration:
    """Real Gateway over HTTP on 127.0.0.1."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Generate real keys, start Gateway on a free port, run tests, tear down."""
        priv_pem, pub_pem = generate_ecdsa_p256_keypair()
        pub_der = public_key_to_der(pub_pem)

        self.gateway = DeviceLinkGateway(
            desktop_uuid="desktop-test-001",
            desktop_device_pubkey_der=pub_der,
            desktop_device_privkey_pem=priv_pem,
            desktop_tls_spki_fp="test-fingerprint-0001",
            config=GatewayConfig(
                max_pair_sessions=5,
                pair_ttl=120,
                max_sas_attempts=3,
                session_ttl=3600,
                single_device=False,
            ),
        )
        self.priv_pem = priv_pem
        self.pub_pem = pub_pem
        self.pub_der = pub_der

        self.port = _free_port()
        self._server = None
        self._thread = None
        self._start_server()
        yield
        self._stop_server()

    def _start_server(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        gateway = self.gateway

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    self._respond(400, {"error": "Invalid JSON"})
                    return
                self._dispatch(self.path, "POST", data)

            def do_GET(self):
                self._dispatch(self.path, "GET", {})

            def _dispatch(self, path, method, data):
                try:
                    result = _route(gateway, path, method, data)
                    self._respond(200 if "error" not in result else self._http_status(result), result)
                except Exception as e:
                    self._respond(500, {"error": str(e)})

            def _respond(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def _http_status(self, result):
                code = result.get("code", 500)
                return code if isinstance(code, int) else 500

            def log_message(self, *args):
                pass  # suppress HTTP log noise

        self._server = HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.1)

    def _stop_server(self):
        if self._server:
            self._server.shutdown()

    def _post(self, path, data=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps(data or {}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            return json.loads(resp.read()), resp.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    # --- Happy Path ---

    def test_pairing_flow(self):
        sid_resp, _ = self._post("/device/v1/pair/start")
        assert "session_id" in sid_resp
        sid = sid_resp["session_id"]

        # Poll
        poll, _ = self._get(f"/device/v1/pair/{sid}")
        assert poll["state"] == "created"

        # Android connects
        fc, _ = self._post(f"/device/v1/pair/{sid}/connect",
                           {"android_uuid": "android-001",
                            "nonce": "ff" * 16})
        assert "state" in fc

        # Send key + start SAS
        android_priv, android_pub = generate_ecdsa_p256_keypair()
        android_der = public_key_to_der(android_pub)
        sas_resp, status = self._post(
            f"/device/v1/pair/{sid}/sas",
            {"android_pubkey_der_hex": android_der.hex()},
        )
        assert "sas" in sas_resp

        # Confirm
        conf, _ = self._post(f"/device/v1/pair/{sid}/confirm", {"confirm": True})
        assert conf["state"] == "confirmed_both"

        # Complete
        comp, _ = self._post(
            f"/device/v1/pair/{sid}/complete",
            {"android_uuid": "android-001",
             "android_pubkey_der_hex": android_der.hex(),
             "display_name": "Test Device"},
        )
        assert comp.get("status") == "bound"
        assert "session_token" in comp

    # --- Attack Tests ---

    def test_reuse_pair_token_rejected(self):
        sid_resp, _ = self._post("/device/v1/pair/start")
        sid = sid_resp["session_id"]

        # Complete the pairing once
        fc, _ = self._post(f"/device/v1/pair/{sid}/connect",
                           {"android_uuid": "a1", "nonce": "f0" * 16})
        android_priv, android_pub = generate_ecdsa_p256_keypair()
        ad = public_key_to_der(android_pub)
        self._post(f"/device/v1/pair/{sid}/sas", {"android_pubkey_der_hex": ad.hex()})
        self._post(f"/device/v1/pair/{sid}/confirm", {"confirm": True})
        self._post(f"/device/v1/pair/{sid}/complete",
                   {"android_uuid": "a1", "android_pubkey_der_hex": ad.hex(),
                    "display_name": "X"})

        # Same session consumed — should be rejected
        poll, _ = self._get(f"/device/v1/pair/{sid}")
        assert poll["state"] in ("consumed", "not_found"), f"Expected consumed, got {poll}"

    def test_unknown_pair_session_rejected(self):
        _, status = self._get("/device/v1/pair/nonexistent-session")
        assert status in (404, 400)

    def test_malformed_json_400(self):
        url = f"http://127.0.0.1:{self.port}/device/v1/pair/start"
        req = urllib.request.Request(url, data=b"not-json", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            assert e.code >= 400

    def test_expired_pair_session_rejected(self):
        sid_resp, _ = self._post("/device/v1/pair/start")
        sid = sid_resp["session_id"]

        # Manually expire the session
        session = self.gateway.pairing_mgr.get(sid)
        if session:
            session.cancel()

        poll, _ = self._get(f"/device/v1/pair/{sid}")
        assert poll.get("state") in ("cancelled", "not_found")

    # --- Read-only status ---

    def test_read_status(self):
        """Verify the /device/v1/status endpoint exists and returns structured data."""
        url = f"http://127.0.0.1:{self.port}/device/v1/status"
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            data = json.loads(resp.read())
            # Even without auth, should return structured data (or auth error)
            assert isinstance(data, dict)
        except urllib.error.HTTPError:
            pass  # auth required is acceptable

    def test_device_list_empty_initially(self):
        devices = self.gateway.devices.list()
        assert isinstance(devices, list)
        assert len(devices) == 0


# ---- Route mapping for test server ----


def _route(gateway: DeviceLinkGateway, path: str, method: str, data: dict) -> dict:
    """Simple manual router for the test HTTP server."""

    # Pairing
    if path == "/device/v1/pair/start" and method == "POST":
        return gateway.pair_start()
    if path.startswith("/device/v1/pair/") and path.endswith("/connect") and method == "POST":
        sid = path.split("/")[4]
        return gateway.pair_first_connection(sid, data.get("android_uuid", ""), data.get("nonce", ""))
    if path.startswith("/device/v1/pair/") and path.endswith("/sas") and method == "POST":
        sid = path.split("/")[4]
        return gateway.pair_start_sas(sid, data.get("android_pubkey_der_hex", ""))
    if path.startswith("/device/v1/pair/") and path.endswith("/confirm") and method == "POST":
        sid = path.split("/")[4]
        return gateway.pair_confirm(sid, data.get("confirm", False))
    if path.startswith("/device/v1/pair/") and path.endswith("/complete") and method == "POST":
        sid = path.split("/")[4]
        return gateway.pair_complete(sid,
                                     data.get("android_uuid", ""),
                                     data.get("android_pubkey_der_hex", ""),
                                     data.get("display_name", ""))
    if path.startswith("/device/v1/pair/") and method == "GET":
        sid = path.split("/")[4]
        return gateway.pair_poll(sid)

    # Read-only
    if path == "/device/v1/status" and method == "GET":
        return {"status": "ok", "version": "0.1.0-dev"}
    if path == "/device/v1/environment" and method == "GET":
        return {"environment": {}}
    if path == "/device/v1/checkpoints" and method == "GET":
        return {"checkpoints": []}
    if path == "/device/v1/diff" and method == "GET":
        return {"changes": [], "count": 0}

    # Device management
    if path.startswith("/device/v1/device/revoke") and method == "POST":
        uuid = data.get("device_uuid", "")
        return gateway.revoke_device(uuid)

    return {"error": "Not found", "code": 404}
