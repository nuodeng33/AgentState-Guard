# AgentState Guard — MVP Specification

## Goal
Installable Desktop app + Android APK that pair over LAN, show env state, and let users configure their own AI model for analysis.

## Phase B: Desktop
- Tauri 2 + React/Vite + Python sidecar
- Pages: Dashboard, Environment, Checkpoints, Changes, Devices, AI Monitor, Settings
- No browser-based UI — real desktop window

## Phase C: AI Provider (buildable now)
- OpenAI-compatible HTTP provider
- User configures: base_url, api_key, model
- Provider presets: DeepSeek, Custom
- Test connection button
- Analyze environment: structured context → AI analysis
- API Key stays in memory only (no disk persistence MVP)
- NEVER sends: .env, private keys, tokens, un-sanitized config

## Phase D: Android
- Kotlin + Jetpack Compose (NOT WebView)
- Read-only: Dashboard, Environment, Checkpoints, Changes, AI
- QR pairing only (no manual IP)
- API Key never touches Android

## Phase E: Pairing
- Desktop shows QR with protocol + UUID + LAN IP + port + pairing id
- Android scans, connects via existing gateway
- SAS verification, device binding
- Auto-reconnect on re-launch

## Phase F: Packaging
- Windows .exe via Tauri
- Android APK

## Completion
Desktop starts, Android starts, QR pairs, status/checkpoints visible, AI analysis works. 
