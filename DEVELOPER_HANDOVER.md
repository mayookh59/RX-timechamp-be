# TrackMe — Production Deployment Handover

**For the developer doing the deployment.** Read this top-to-bottom once before
clicking anything. Total deploy time **~45 minutes**.

---

## 1. Architecture & Hosting Plan

```
┌──────────────────┐      HTTPS       ┌──────────────────┐
│  Dashboard (FE)  │ ───────────────▶ │  Backend (BE)    │
│   Vite + React   │                  │  FastAPI         │
│   on VERCEL      │ ◀─── CORS ───── │  on RAILWAY      │
└──────────────────┘                  └──────┬───────────┘
                                              │
                                              ▼
                                     ┌──────────────────┐
                                     │  PostgreSQL      │
                                     │  (Railway addon) │
                                     └──────────────────┘
        ▲
        │ HTTPS
        │
┌───────┴──────────┐
│ Agents (Win/Mac) │
│ on user machines │
└──────────────────┘
```

### Why this split?

| Component | Hosting | Why |
|---|---|---|
| **Dashboard (Vite/React static)** | **Vercel** | Free tier is generous; CDN-served; fast worldwide |
| **Backend (FastAPI uvicorn)** | **Railway** | Long-running Python process; Vercel's serverless model fights with FastAPI's startup, threadpools, agent log buffer cache |
| **Database (PostgreSQL)** | **Railway addon** | Same network as backend; free $5 credit/mo |
| **Agents** | User machines | Already production-ready (v2.0.0), no hosting needed |

> **Don't try to put the FastAPI backend on Vercel.** It can technically run as a
> serverless function, but: (a) every request is a cold start, (b) the in-memory
> heartbeat telemetry cache (`_latest_telemetry` in `agents.py`) won't persist
> between requests, (c) screenshot upload (10–30 s) hits Vercel's 10 s timeout.

---

## 2. Repository Structure

```
TRACKER.AI/
├── backend/              ← FastAPI app (deploy to Railway)
│   ├── app/              ← application code
│   ├── bootstrap.py      ← runs once on first deploy, creates tables + admin user
│   ├── requirements.txt
│   ├── Procfile          ← used by Railway (web: uvicorn ...)
│   ├── Dockerfile        ← alternate path if Railway/Render uses Docker
│   ├── runtime.txt       ← python-3.10.9
│   └── .env.example      ← reference for Railway env vars
├── dashboard/            ← React/Vite app (deploy to Vercel)
│   ├── src/
│   ├── package.json
│   ├── vite.config.ts
│   ├── vercel.json       ← Vercel build config (already correct)
│   └── .env.example      ← reference for Vercel env vars
├── agent/                ← Python tracking agents (no hosting needed)
│   ├── trackme_agent.py        ← Windows agent v2.0.0
│   ├── trackme_agent_mac.py    ← Mac agent v2.0.0
│   ├── install_windows.bat
│   └── install_mac.sh
├── railway.json          ← Railway build/deploy config
└── DEVELOPER_HANDOVER.md ← this file
```

---

## 3. Step-by-Step Deployment

### Phase 1 — Get the code on GitHub (5 min)

The repository must be on GitHub for Vercel and Railway to pull from.

```bash
cd /path/to/TRACKER.AI

# Verify there are no secrets about to push
git status --short | grep -iE "\.env$|\.env\.local|\.pem|\.key" && echo "STOP — secrets detected"

# If a remote isn't set up yet:
git remote add origin https://github.com/YOUR-USERNAME/trackme.git
git push -u origin main
```

If the repo is private, both Vercel and Railway support private GitHub repos —
just authorize them during signup.

---

### Phase 2 — Deploy Backend to Railway (10 min)

#### 2.1. Create the Railway project

1. Go to https://railway.app → **Login with GitHub**
2. **+ New Project** → **Deploy from GitHub repo** → select `trackme`
3. Railway auto-detects `railway.json` and starts building. The build will fail
   the first time because `DATABASE_URL` isn't set yet — that's expected.

#### 2.2. Add PostgreSQL

1. Inside the project canvas → **+ New** → **Database** → **Add PostgreSQL**
2. Railway automatically injects `DATABASE_URL` into the backend service
3. The backend's [`app/core/config.py`](backend/app/core/config.py) auto-converts
   Railway's `postgres://` URL to `postgresql+asyncpg://` and strips `sslmode`
   parameters that asyncpg doesn't support — **no code change needed**.

#### 2.3. Set environment variables

Click the backend service → **Variables** → **+ New Variable**. Add these:

| Variable | Value | Notes |
|---|---|---|
| `SECRET_KEY` | *(generate fresh)* | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ORIGINS` | `*` | Lock to your Vercel URL once known (Phase 3) |
| `LOG_LEVEL` | `INFO` | Optional |
| `ADMIN_EMAIL` | `you@yourdomain.com` | Admin login email (default: admin@trackme.com) |
| `ADMIN_PASSWORD` | *(strong password)* | **Change after first login** |

`DATABASE_URL` is set automatically by the Postgres addon — don't add it manually.

#### 2.4. Deploy

After the env vars are set, Railway will redeploy automatically. Watch the
deploy logs — you should see:

```
[bootstrap] Creating tables (if missing)…
[bootstrap] Tables ready.
[bootstrap] Org created: TrackMe
[bootstrap] Admin created: you@yourdomain.com / YOUR_PASSWORD
INFO:     Uvicorn running on http://0.0.0.0:8080
```

`bootstrap.py` is idempotent — runs once, then becomes a no-op on every
subsequent deploy.

#### 2.5. Generate a public domain

Backend service → **Settings** → **Networking** → **Generate Domain**.

You get something like `https://trackme-production-XXXX.up.railway.app`.

**Test it:**
```bash
curl https://trackme-production-XXXX.up.railway.app/health/live
# Expect: {"status":"ok"}
curl https://trackme-production-XXXX.up.railway.app/docs
# Expect: HTTP 200, FastAPI Swagger HTML
```

**Save this URL — you'll need it in Phase 3.**

#### 2.6. Optional: custom domain

Settings → Networking → **+ Custom Domain** → enter `api.yourdomain.com`. Add
the CNAME record in your DNS as instructed. SSL is automatic via Let's Encrypt.

---

### Phase 3 — Deploy Dashboard to Vercel (5 min)

#### 3.1. Create the Vercel project

1. Go to https://vercel.com → **Login with GitHub**
2. **Add New** → **Project** → import the `trackme` repo
3. Configure:
   - **Root Directory**: `dashboard` ← important
   - **Framework Preset**: Vite (auto-detected)
   - **Build Command**: `npm run build` (default)
   - **Output Directory**: `dist` (default)

#### 3.2. Set environment variables

Before clicking Deploy, click **Environment Variables** and add:

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://trackme-production-XXXX.up.railway.app/api/v1` |

Use the Railway URL from Phase 2.5, **with `/api/v1` appended**.

#### 3.3. Deploy

Click **Deploy**. After ~30 s you get `https://trackme-XXXX.vercel.app`.

#### 3.4. Lock down CORS on the backend

Now that you know the Vercel URL, go back to **Railway → backend service →
Variables** and update:

```
CORS_ORIGINS=https://trackme-XXXX.vercel.app
```

(Replace `*` from earlier — `*` was just to avoid blocking initial testing.)

Railway redeploys automatically. ~30 s later, the backend only accepts requests
from your dashboard.

#### 3.5. Test login

Open `https://trackme-XXXX.vercel.app`. Sign in with the admin credentials you
set in Phase 2.3 (`ADMIN_EMAIL` / `ADMIN_PASSWORD`).

If login works → **the platform is live**.

If you see "Could not load dashboard data" → check browser console. 99% of the
time it's:
- CORS misconfiguration → check `CORS_ORIGINS` matches the Vercel URL exactly (no trailing slash)
- Wrong API URL → check `VITE_API_BASE_URL` ends with `/api/v1` (with `/v1`)

---

### Phase 4 — Update agents to use the new backend URL (5 min)

The agent default URL is currently `https://trackme.example.com/api/v1` (a
placeholder). Replace it with the Railway URL.

In [`agent/trackme_agent.py`](agent/trackme_agent.py) and
[`agent/trackme_agent_mac.py`](agent/trackme_agent_mac.py), find:

```python
DEFAULT_CONFIG: dict[str, Any] = {
    "api_base_url": "https://trackme.example.com/api/v1",
    ...
}
```

Replace with:

```python
DEFAULT_CONFIG: dict[str, Any] = {
    "api_base_url": "https://trackme-production-XXXX.up.railway.app/api/v1",
    ...
}
```

Commit and push:

```bash
git add agent/trackme_agent.py agent/trackme_agent_mac.py
git commit -m "Point agents at production backend"
git push
```

The backend automatically serves these scripts via `/agents/download-script` and
`/agents/download-script-mac`, so any new install pulls the updated code.

---

### Phase 5 — Onboard a user (1 min)

1. Open the dashboard, log in as admin.
2. Click **Add Employee** → enter their name + email + role.
3. Send them this command (Windows):

   ```cmd
   curl -o %TEMP%\install.bat https://trackme-production-XXXX.up.railway.app/api/v1/agents/installer-windows && %TEMP%\install.bat https://trackme-production-XXXX.up.railway.app/api/v1 their.email@whatever.com
   ```

   Or Mac:

   ```bash
   curl -sSL https://trackme-production-XXXX.up.railway.app/api/v1/agents/installer-mac | bash -s -- https://trackme-production-XXXX.up.railway.app/api/v1 their.email@whatever.com
   ```

4. They run it once. Within 60 s the device shows online on the dashboard.

> **Note:** The installer routes (`/agents/installer-windows`, `/agents/installer-mac`)
> currently don't exist as endpoints. The installers live in `agent/install_windows.bat`
> and `agent/install_mac.sh` in the repo. Either:
> - Have users download the installer from a separate URL (Vercel can host
>   static files: drop them in `dashboard/public/install_windows.bat`)
> - Or add a simple FastAPI route to serve them — see Phase 6 below

---

### Phase 6 — Optional polish

#### 6.1. Add installer-serving routes to backend

Add to `backend/app/api/v1/agents.py`:

```python
from fastapi.responses import FileResponse

@router.get("/installer-windows")
async def download_windows_installer():
    paths = [
        Path(__file__).resolve().parents[4] / "agent" / "install_windows.bat",
        Path(__file__).resolve().parents[3] / "agent" / "install_windows.bat",
    ]
    for p in paths:
        if p.exists():
            return FileResponse(str(p), media_type="application/x-bat",
                                filename="install_windows.bat")
    raise HTTPException(404, "Installer not found")

@router.get("/installer-mac")
async def download_mac_installer():
    paths = [
        Path(__file__).resolve().parents[4] / "agent" / "install_mac.sh",
        Path(__file__).resolve().parents[3] / "agent" / "install_mac.sh",
    ]
    for p in paths:
        if p.exists():
            return FileResponse(str(p), media_type="text/x-shellscript",
                                filename="install_mac.sh")
    raise HTTPException(404, "Installer not found")
```

#### 6.2. Move screenshots to S3 / Cloudflare R2

Right now screenshots are saved to `backend/screenshots_store/` — Railway's
ephemeral filesystem. They survive single deploys but not container restarts.

Set these env vars on Railway to enable S3 storage:

```
S3_ENDPOINT=https://YOUR-ACCOUNT.r2.cloudflarestorage.com
S3_BUCKET=trackme-screenshots
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

(Cloudflare R2 is free for 10 GB; AWS S3 costs ~$0.023/GB/mo.)

#### 6.3. Set up daily Postgres backups

Railway → PostgreSQL service → **Backups** → enable. Pro plan ($5/mo) includes
daily backups. Or, on the free tier, run `pg_dump` on a cron via Railway CLI:

```bash
railway run --service postgres pg_dump $DATABASE_URL > backup-$(date +%F).sql
```

---

## 4. Reference

### Backend env vars (Railway)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | ✅ (auto) | — | Set by Railway PostgreSQL addon |
| `SECRET_KEY` | ✅ | random insecure | JWT signing key — **must change for production** |
| `CORS_ORIGINS` | ✅ | `localhost:5173,3000` | Comma-separated list of allowed dashboard URLs |
| `LOG_LEVEL` | — | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `ADMIN_EMAIL` | — | `admin@trackme.com` | Initial admin user (created by `bootstrap.py`) |
| `ADMIN_PASSWORD` | — | `admin123` | **Change after first login from dashboard** |
| `ALGORITHM` | — | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | — | `15` | JWT lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | — | `7` | Refresh token lifetime |
| `S3_BUCKET` | — | — | Optional: S3 bucket for screenshot storage |
| `S3_ENDPOINT` | — | — | Optional: S3 endpoint (Cloudflare R2 / MinIO / AWS) |
| `AWS_ACCESS_KEY_ID` | — | — | Optional: S3 credentials |
| `AWS_SECRET_ACCESS_KEY` | — | — | Optional: S3 credentials |

### Dashboard env vars (Vercel)

| Variable | Required | Example |
|---|---|---|
| `VITE_API_BASE_URL` | ✅ | `https://api.example.com/api/v1` |

---

## 5. Verification Checklist

After deployment, verify each:

- [ ] `curl https://YOUR-RAILWAY.up.railway.app/health/live` → 200 OK
- [ ] `curl https://YOUR-RAILWAY.up.railway.app/docs` → FastAPI Swagger loads
- [ ] Browse to `https://YOUR-VERCEL.vercel.app` → login page renders
- [ ] Login with admin credentials → dashboard loads (no "Network Error")
- [ ] Browser DevTools → Network tab → API requests go to Railway URL with HTTPS
- [ ] Click **Add Employee** → user appears in Team Dashboard
- [ ] Install agent on a test machine — appears in dashboard within 60 s
- [ ] Check `https://YOUR-RAILWAY.up.railway.app/api/v1/admin/agents` (with auth)
      shows the new device

---

## 6. Cost Estimate

For first ~50 users:

| Service | Free tier | Realistic monthly |
|---|---|---|
| Railway backend + PostgreSQL | $5 credit/mo | $0 (within credit) |
| Vercel dashboard | unlimited bandwidth | $0 |
| Domain (optional) | — | ~$10/year |
| **Total** | — | **$0–$1/mo** |

For ~200 users: **$10–$15/mo** (Railway usage scales).

---

## 7. Common Issues & Fixes

| Symptom | Cause | Fix |
|---|---|---|
| Build fails with "Module not found: aiosqlite" | Old requirements.txt | Pull latest — `aiosqlite` is now in [`backend/requirements.txt`](backend/requirements.txt) |
| Login: "Network Error" | CORS or wrong API URL | Check `CORS_ORIGINS` includes Vercel URL; check `VITE_API_BASE_URL` includes `/api/v1` |
| Login: 401 unauthorized | Wrong admin password | Reset by changing `ADMIN_PASSWORD` env var on Railway, then in Railway shell: `python backend/bootstrap.py` (won't recreate user, but you can reset by deleting the user row first) |
| Agents stuck "Network unreachable" | Old config pointing at LAN IP | Edit `~/Library/Application Support/TrackMe/config.json` (Mac) or `C:\ProgramData\TrackMe\config.json` (Win): change `api_base_url` to Railway URL |
| Screenshots disappear after deploy | Local disk is ephemeral on Railway | Configure S3/R2 (Phase 6.2) |
| Agent build fails on Windows | Python missing | The installer auto-downloads Python 3.11 — make sure the user runs as Administrator |

---

## 8. Post-Deploy Hardening (do these within first week)

1. **Change admin password** from the Profile page (don't keep `admin123`)
2. **Lock down CORS** to specific Vercel URL only (no `*`)
3. **Enable Railway daily backups** (Pro plan, $5/mo)
4. **Set up Sentry** error tracking — add `SENTRY_DSN` env var
5. **Move screenshots to S3** so they survive container restarts
6. **Add 2FA** for admin accounts (not yet implemented in dashboard)
7. **Audit `users` table** — remove test users (`admin@trackme.com` if you set
   a different `ADMIN_EMAIL`)

---

## 9. Files the developer needs

Everything is in this repo. Key files:

| Purpose | File |
|---|---|
| Backend deploy config | [`railway.json`](railway.json), [`backend/Procfile`](backend/Procfile), [`backend/Dockerfile`](backend/Dockerfile) |
| Backend bootstrap | [`backend/bootstrap.py`](backend/bootstrap.py) |
| Backend env reference | [`backend/.env.example`](backend/.env.example), [`backend/.env.railway`](backend/.env.railway) |
| Frontend deploy config | [`dashboard/vercel.json`](dashboard/vercel.json) |
| Frontend env reference | [`dashboard/.env.example`](dashboard/.env.example) |
| Agent source | [`agent/trackme_agent.py`](agent/trackme_agent.py), [`agent/trackme_agent_mac.py`](agent/trackme_agent_mac.py) |
| Agent installers | [`agent/install_windows.bat`](agent/install_windows.bat), [`agent/install_mac.sh`](agent/install_mac.sh) |

---

## 10. After You're Live — Rollback Plan

- **Bad backend deploy?** Railway → Deployments → click any past deployment →
  **Redeploy**. Reverts in 30 s.
- **Bad dashboard deploy?** Vercel keeps every deploy → **Promote to Production**
  on any prior deployment. Same idea.
- **Database disaster?** If you enabled backups, restore from the Postgres
  service's Backups tab. Without backups, hope the Railway team has internal
  snapshots — contact support.

---

## Questions / Issues

If anything in this doc is unclear or wrong, ping the original maintainer
(see git log). The full architecture rationale is in
[`GO_LIVE.md`](GO_LIVE.md) and the agent internals are in
[`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md).
