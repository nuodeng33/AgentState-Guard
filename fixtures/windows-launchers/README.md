# Windows launcher-shape regression fixtures

Physical Windows dogfood (MSI `62aff59`) showed that node-hosted Agents
(Claude Code, Codex, Kimi Code) are **not** always reached through a direct
`node.exe …\node_modules\…\cli.js` argv shape, so bounded launcher identity
must stay fail-closed yet correctly resolve the standard Windows launcher
forms.

The bounded reducer never returns raw argv and never returns install paths —
it returns only an on-disk script anchor (`node_modules/<pkg>/…/*.js|cjs|mjs`)
that `launcher_identity.resolve_launcher_identity` maps to a package identity.

Canonical npm Windows layouts (unprivileged `%APPDATA%\npm` prefix is the
Windows default):

| Shape | What `psutil … cmdline()` typically yields | Bounded expectation |
| --- | --- | --- |
| direct exe (`codex.exe`, `claude.exe`, `kimi.exe`) | native executable basename | basename signature (no launcher reducer) |
| npm `.cmd` shim (`%APPDATA%\npm\codex.cmd`) | `node.exe C:\…\npm\node_modules\@openai\codex\dist\cli.js …` | anchor `@openai/codex/dist/cli.js` → CODEX |
| node + JS entry | `node.exe C:\…\npm\node_modules\@moonshot-ai\kimi-code\cli.js …` | anchor `…\cli.js` → KIMI_CODE |
| package bin launcher | `node.exe C:\…\npm\node_modules\@anthropic-ai\claude-code\cli.js` | anchor `…\cli.js` → CLAUDE |
| bundled/native executable | interpreted exe with no node argv | no anchor → no launcher identity |
| ambiguous generic node | `node.exe server.js --port 8080` | no anchor → must stay unclassified |
