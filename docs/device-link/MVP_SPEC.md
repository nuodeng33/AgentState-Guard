# Device Link v1 MVP — Read-Only Android Client

## Goal
Android within same LAN: discover → pair → bind → auto-reconnect → read env status.

## Out of Scope (→ docs/device-link/FUTURE.md)
Remote restore, remote undo, remote transactions, biometric auth,
multiple devices, background connection, WebSocket streaming,
incident/handoff creation, checkpoint creation, Windows firewall,
network profile enforcement, relay/STUN/TURN/Internet access.

## MVP Requirements (14)
| ID | Description |
|----|-------------|
| DL-MVP-001 | Desktop ECDSA P-256 device identity |
| DL-MVP-002 | Android ECDSA P-256 device identity (Keystore) |
| DL-MVP-003 | mDNS discovery (_agentstate._tcp) |
| DL-MVP-004 | QR pairing with canonical transcript |
| DL-MVP-005 | SAS verification (HMAC-SHA256 derived) |
| DL-MVP-006 | TLS protected transport |
| DL-MVP-007 | Public-key device binding |
| DL-MVP-008 | Replay protection (expiry + nonce cache) |
| DL-MVP-009 | Auto LAN reconnect |
| DL-MVP-010 | Device revocation |
| DL-MVP-011 | Read status |
| DL-MVP-012 | Read environment |
| DL-MVP-013 | List checkpoints |
| DL-MVP-014 | Sanitized config diff |
