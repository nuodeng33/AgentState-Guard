# Privacy Audit Final — AgentState Guard 0.9.0.dev0

- No telemetry, no analytics, no external service calls
- No data leaves the local machine
- Host-import field whitelist enforced server-side
- Web UI is fully local (no CDN, all assets bundled)
- No cookies set by the application
- No tracking pixels or beacons
- Incident bundles are sanitized before output
- Handoff docs contain no credentials or raw secrets
- User data: only .agentguard/ directory with checkpoint snapshots
- No cloud sync, no SaaS backend
