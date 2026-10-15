# Deploying the demo on a container host

The app is one container plus one persistent volume. Sessions, saved cases, runs and the daily Claude budget all live in a SQLite file on that volume, so the host must give the container a disk that survives restarts and redeploys. Any host that runs a Docker image and offers a persistent volume works. Check each provider's current terms, limits and prices yourself before choosing: none were verified for this guide.

## What the host must offer

| Need | Why |
|---|---|
| Runs a Docker image | The `Dockerfile` is the whole build. |
| A persistent volume mounted at `/data` | `RUNS_DB=/data/runs.db`. Without it, every restart ends all sessions and resets the daily budget. |
| One machine (one instance) | SQLite on a volume belongs to one machine. Do not scale to several machines. Two workers inside the container are fine. |
| HTTPS in front (a managed proxy or your own) | The sign-in cookie and the password must not travel in clear text. |
| Secrets as environment variables | Never put them in the image, the repo or a zip. |
| Request time of at least 3 minutes | A Claude call with tools can take a while (`GUNICORN_TIMEOUT`, default 180 s). Some proxies cut requests earlier; check yours. |

## Settings

Set these as the host's secrets or environment variables.

| Variable | Value |
|---|---|
| `DEMO_USERNAME`, `DEMO_PASSWORD` | The shared demo login. Use a long password (12+ characters). |
| `FLASK_SECRET_KEY` | A long random value (24+ characters). Changing it signs everyone out. |
| `ANTHROPIC_API_KEY` | A separate key made for this demo, with a monthly spend limit set in the Anthropic console. Leave it unset to run rules-only. |
| `MODEL_CALLS_ENABLED` | `true`, or `false` to switch every Claude path off at once. |
| `DATASET` | The dataset a new sign-in starts on (`dev`, `extended` or `edge`). |
| `RUNS_DB` | `/data/runs.db` (already set in the image). |
| `COOKIE_SECURE` | `true`. |
| `TRUST_PROXY_HOPS` | `1` when exactly one proxy sits in front. Also set `FORWARDED_ALLOW_IPS` to the proxy's address if it is not local. Without this, the login throttle sees every visitor as the proxy. |

The daily caps (`DAILY_CALLS_GLOBAL`, `DAILY_CALLS_PER_SESSION`) and the day's time zone are in `config.py`.

## First deploy

1. Run `python scripts/preflight.py` with the production settings loaded. Fix every ERROR; read every warning.
2. Build and start the container with the volume and the settings above. The health check (`/healthz`) turns healthy only when the three login settings are present and the database answers. It never calls Claude.
3. Run the smoke test from your own machine:
   `DEMO_PASSWORD=... python scripts/smoke_test.py https://your-host --username NAME`
   All lines must say PASS. It signs in, switches datasets, reads the early-warning page and the budget, checks that a request without a token is refused, and signs out. It makes no Claude call.
4. Open the site in a browser, sign in, switch the dataset from the header, and look at the daily count in the header.
5. Before sharing the link: confirm the spend limit on the key, confirm `MODEL_CALLS_ENABLED`, and try one question on the Ask page with the key set. The live Claude paths have never been run against a real key in development, so do this once yourself and watch the Claude runs page.

## Day to day

- **Stop all Claude spending now:** set `MODEL_CALLS_ENABLED=false` and restart. Rules-only pages keep working.
- **Change who can sign in:** change `DEMO_USERNAME` and `DEMO_PASSWORD`, restart. Existing sessions stay valid until they expire (12 hours) or you change `FLASK_SECRET_KEY`.
- **Back up:** copy the database with SQLite's own command so the copy is consistent while the app runs:
  `sqlite3 /data/runs.db ".backup '/data/backup.db'"`, then copy that file off the volume.
- **Roll back:** redeploy the previous image. The database schema only gains tables and columns, and older code ignores them.
- **Logs:** access lines show the client address, the request line, the status, the size and the time. They contain no cookies or headers. The model key is never logged.

## If something is wrong

| Symptom | Likely cause |
|---|---|
| Health check fails and every page says "not configured" (503) | One of `DEMO_USERNAME`, `DEMO_PASSWORD`, `FLASK_SECRET_KEY` is missing. |
| Health check says "database unavailable" | The volume is missing, read-only or full. |
| Everyone is locked out after a few wrong passwords | The login throttle counts per client address; without `TRUST_PROXY_HOPS` all visitors share the proxy's address. |
| Signed in, but the next page asks to sign in again | `COOKIE_SECURE=true` on a site served over plain http. Use https. |
| Sessions vanish after a restart | `/data` is not a persistent volume. |
| Claude pages say "analysis limit" | The daily cap, the kill switch, or a counter that cannot be reached; the message says which. |

## Known limits

- One machine only (SQLite on one volume).
- Scripts that send a Basic header share one simulated clock per worker process. Use a signed-in session for anything that moves the clock.
- One shared login means the daily global cap is what protects the key. Signing out and in again gives a new per-session allowance.
- The image build has not been run in the environment this was written in (no Docker daemon there); the Dockerfile, compose file, preflight and smoke test were checked in other ways.
