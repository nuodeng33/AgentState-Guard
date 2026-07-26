# Device Link UX Flows

> **FUTURE UX.** This flow is not physical end-to-end evidence for P0-A.

## Desktop — First Launch
```
App opens → Dashboard
           → Sidebar: [Devices]
Devices page:
  "Mobile Link: OFF"
  [Enable Mobile Link]
→ Generates Ed25519 identity (if first time)
→ Starts mDNS advertisement
  "Mobile Link: ON"
  "Listening on Private LAN"
  "No paired devices"
  [Pair Android Device]
```

## Desktop — Pairing
```
[Pair Android Device] clicked
→ QR Code shown (120s countdown)
→ SAS: "482 913" displayed
→ [Confirm] [Reject]
→ User confirms → "Pairing successful"
→ "Pixel 10 — CONNECTED"
```

## Android — First Launch
```
App opens → "AgentState Guard"
           → "Searching for your computers..."
           → NsdManager discovers:
               DESKTOP-ABC
               AgentState Guard
               Unpaired
               [Connect]
```

## Android — Pairing
```
[Connect] tapped → Camera opens for QR + SAS step
  QR scanned → "Pair with DESKTOP-ABC?"
  "482 913"
  [Confirm] [Cancel]
→ Both confirm → "Connected to DESKTOP-ABC"
→ Home screen with environment status
```

## Android — Reconnecting
```
App opens → mDNS found UUID match
→ TLS verify (cert fingerprint match)
→ Challenge auth (invisible to user, <500ms)
→ "Connected to DESKTOP-ABC"
→ Home screen
(No QR, no SAS, no manual input)
```

## Android — IP Changed
```
Desktop IP: 192.168.1.21 → 192.168.50.104
mDNS TXT record updated with new endpoint hint
Android discovers same UUID, same pubkey fingerprint
→ Reconnect automatically
(No re-pairing required)
```

## Desktop — Revoke Device
```
Settings → Mobile Devices
Pixel 10 — CONNECTED
[Revoke]
→ Confirm dialog: "Revoke Pixel 10?"
[Revoke] [Cancel]
→ Device immediately removed
→ Android receives: PAIRING REVOKED (if connected)
→ Android app returns to pairing screen
```

## Android — High Risk Operation
```
[Restore] tapped
→ "This will restore settings.json to checkpoint #3"
→ "Rollback coverage: 100%"
→ "This action requires confirmation"
→ Android biometric/PIN prompt
→ [Confirm Restore]
→ Desktop executes with whitelist check
→ Result displayed: ✅ Restored / ❌ Failed
```
