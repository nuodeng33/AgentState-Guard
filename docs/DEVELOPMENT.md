# Source and cloud development

Updated 2026-10-15. Use the branch containing the dated workspace update; an old
default-branch checkout or published package may not contain these modules.

## Checkout and install

```bash
git clone --branch feat/v1-host-native-closure-sol https://github.com/nuodeng33/AgentState-Guard.git
cd AgentState-Guard
python -m venv .venv
# Linux/macOS; on Windows use .venv\Scripts\Activate.ps1
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd web
npm ci
npm test
npm run build
cd ..
python -m pytest tests/ -q
```

Python 3.11+ and Node.js 22 are the existing CI baselines. GitHub Actions runs the
Core tests, frontend checks and wheel build. Desktop/Android checks have their
own platform requirements. Live Docker/WSL and provider checks require explicit
opt-in and access to those actual environments; do not set live-test variables
in an ordinary cloud unit-test job.

## Run locally

```bash
python -m agentguard ui --port 8787
```

Open `http://127.0.0.1:8787/?workspace=1`. For frontend iteration, run `npm run dev`
inside `web/`; its `/api` proxy targets the Core on port 8787. Cloud editing,
unit tests and builds work without the author's machine or its data. A remote
cloud runner cannot inspect or control the Windows/Docker/WSL environments on
your personal computer; actual provider credentials and permissions must be
configured in the intended execution environment. Keep the Core private; this
guide does not enable public hosting or remote execution access.

## Browser feedback checks without model calls

Install Playwright in a separate tooling environment and its Chromium browser.
Set `PLAYWRIGHT_MODULE` to that module's absolute path if it is not resolvable
from the checkout; optionally set `CHROME_PATH` to an existing Chromium/Chrome
executable. No user-specific absolute module paths are embedded in the script.

```bash
cd web
npx vite build --outDir ../artifacts/work-feedback/dist
cd ..
node scripts/verify_work_feedback_browser.cjs
```

The script owns a temporary random-port server, uses explicit API fixtures and
writes ignored screenshots/results into `artifacts/work-feedback/`. It does not
launch a model. `verify_managed_session_live.py` and the managed-session browser
script are separate **real execution** acceptance tools, not ordinary smoke
tests; they require a deliberately prepared environment and may invoke a provider.
The older browser tool also requires a fixture file produced by that preparation.

## Data, upgrades and evidence

- Default runtime state is local; existing preview databases and agent histories
  are intentionally excluded from Git. A fresh clone starts with its own state.
- Back up an existing SQLite database using the SQLite backup API before an
  upgrade, validate a copy first, then start the new Core. Do not copy a live
  database without its transactional backup procedure. The additive upgrade
  helper is `scripts/verify_managed_session_upgrade.py --help`.
- Keep command idempotency, scope validation, fixed input versions and restoration
  preview requirements intact. Read the [architecture](ARCHITECTURE.md),
  [Work API](WORK_API.md), [execution contract](MANAGED_SESSION_BACKEND.md),
  [discovery contract](CORE_DISCOVERY_MANAGEMENT.md), and
  [protection contract](CORE_PROTECTION_BACKEND.md) before extending the relevant seam.
- Dated acceptance documents distinguish historical local checks, fixtures and
  actual provider tests. Referenced ignored reports are local evidence, not files
  available in a clean checkout. Record fresh results for the environment you use.
