# TrackMe — Backend & Agent

Server-side and desktop-agent code for the TrackMe employee monitoring
platform. The dashboard (React frontend) lives in a separate repository.

## What's in here

| Folder | What | Where it deploys |
|---|---|---|
| `backend/` | FastAPI app (Python 3.10+) | Railway |
| `agent/` | Cross-platform tracking agents (Python) + installers | User machines |

## Read this first

👉 **[DEVELOPER_HANDOVER.md](DEVELOPER_HANDOVER.md)** — single source of truth.

It walks through the full deployment in 5 phases:
1. GitHub
2. Railway (backend + PostgreSQL)
3. Vercel (dashboard — separate repo)
4. Update agent URL
5. Onboard users

Estimated time: ~45 minutes start-to-finish.

## Critical heads-up

**Don't put the FastAPI backend on Vercel.**
Vercel's serverless model breaks 3 things in this app (cold starts, 10s
timeout, no persistent process). The backend needs Railway / Render / Fly.io —
something that runs a long-lived Python process. Full reasoning in section 1
of `DEVELOPER_HANDOVER.md`.

## Other reference docs

- `GO_LIVE.md` — context, costs, alternative hosting options
- `PRODUCTION_DEPLOYMENT.md` — agent installer details (Scheduled Task / LaunchAgent)
- `SETUP_GUIDE.md` — local development setup
- `docker-compose.yml` — alternative: self-host the entire stack on a VM
  (note: references `../trackme-frontend/dist` for nginx — adjust paths if
  you keep both repos side-by-side, or skip the nginx service)
- `railway.json` — Railway build/deploy config (used automatically)

## Quick start (local development)

```bash
cd backend
python -m venv .venv
. .venv/Scripts/activate        # Windows
# . .venv/bin/activate          # macOS/Linux
pip install -r requirements.txt
cp .env.example .env             # fill in DATABASE_URL etc.
alembic upgrade head
python start_local.py
```

Backend runs on http://localhost:8000.

## Cost summary

For first ~50 users:
- Railway free tier ($5/mo credit) covers backend + Postgres
- Domain (optional, ~$10/year)
- **Total: $0–$1/month**

Scales to **~$10–$15/month at 200 users** before any paid plans needed.

## Related

- Frontend repo: TrackMe dashboard (React/Vite on Vercel) — separate repository.

## Questions

Original maintainer is `hrishikesh@rhythmrx.ai`. Architecture questions are
answered in the docs above.
