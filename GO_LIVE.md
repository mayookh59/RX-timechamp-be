# Going Live — Deploy TrackMe Globally

After this, **anyone, on any network, on any continent** can install the agent
and have it work. No VPN, no LAN restrictions, no port forwarding.

## What you'll have when done

| Component | URL | Hosting |
|---|---|---|
| Backend API | `https://api.trackme.yourdomain.com` (or `https://trackme-yourname.up.railway.app`) | Railway |
| Dashboard | `https://app.trackme.yourdomain.com` | Vercel / Cloudflare Pages |
| Database | Managed PostgreSQL with daily backups | Railway plugin |
| Agent installer download | Same as backend (`/api/v1/agents/download-script`) | Railway |

Total cost: **$0–10/month** depending on usage.

---

## Phase 1 — Deploy backend to Railway (10 min)

### 1.1 Push code to GitHub

```bash
git add .
git commit -m "Production deployment prep"
git push origin main
```

### 1.2 Create Railway project

1. Go to https://railway.app, sign in with GitHub.
2. **New Project → Deploy from GitHub repo** → pick your repo.
3. Railway reads `railway.json` automatically and starts building.
4. **Add PostgreSQL**: in your project canvas → New → Database → PostgreSQL.
   Railway sets `DATABASE_URL` env var on the backend service automatically.

### 1.3 Set required environment variables

In the backend service → Variables tab, add:

```
SECRET_KEY=<run: openssl rand -hex 32>
CORS_ORIGINS=https://app.trackme.yourdomain.com,https://trackme-yourname.vercel.app
LOG_LEVEL=INFO
```

Optional (for screenshot storage in S3 instead of disk):
```
S3_BUCKET=trackme-screenshots
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_ENDPOINT=https://s3.amazonaws.com
```

### 1.4 Run initial seed (one time)

In Railway dashboard → backend service → Settings → "Deploy" → after first
deploy succeeds, open a shell and run:

```bash
python backend/seed.py
```

This creates the admin user (`admin@trackme.com` / `admin123` — **change the
password immediately** via the dashboard's profile settings).

### 1.5 Generate public domain

Service → Settings → Networking → **Generate Domain**. You get something like
`https://trackme-production.up.railway.app`.

Test it:
```bash
curl https://trackme-production.up.railway.app/health/live
# {"status":"ok"}
```

### 1.6 Optional: custom domain

Buy a domain (Namecheap, Porkbun ~$10/yr). In Railway → Settings → Networking →
Add Custom Domain → enter `api.trackme.yourdomain.com`. Add the CNAME record
in your DNS as instructed. SSL is automatic.

---

## Phase 2 — Deploy dashboard to Vercel (5 min)

1. Sign up at https://vercel.com (GitHub login).
2. Import your repo → Root Directory: `dashboard` → Framework: Vite.
3. Set env var: `VITE_API_BASE_URL=https://trackme-production.up.railway.app/api/v1`.
4. Deploy. You get `https://trackme-yourname.vercel.app`.
5. Optional: add custom domain `app.trackme.yourdomain.com`.

---

## Phase 3 — Update agent default URL

In `agent/trackme_agent.py` and `agent/trackme_agent_mac.py`, change:

```python
DEFAULT_CONFIG: dict[str, Any] = {
    "api_base_url": "https://trackme.example.com/api/v1",  # ← put your real URL here
    ...
}
```

Commit and push — agents pull the latest script from `/agents/download-script`,
so any new install uses the right URL automatically.

---

## Phase 4 — One-line installers (anyone, anywhere)

After Phases 1–3 are done, you can give *anyone* a single line:

### Windows (run in elevated cmd)

```cmd
curl -o %TEMP%\install.bat https://trackme-production.up.railway.app/static/install_windows.bat && %TEMP%\install.bat https://trackme-production.up.railway.app/api/v1 their.email@company.com
```

### Mac (run in Terminal)

```bash
curl -sS https://trackme-production.up.railway.app/static/install_mac.sh | bash -s -- https://trackme-production.up.railway.app/api/v1 their.email@company.com
```

You need to expose the installer scripts via the backend. Add to `app/main.py`:

```python
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="../agent"), name="static")
```

Or serve them from Vercel/Cloudflare Pages as static files.

---

## Phase 5 — Onboarding flow for new users

Currently, agents register with an email. The user must **already exist** in the
database (admin creates them via the "Add Employee" button on the dashboard).
The flow:

1. **You** (admin) log into the dashboard.
2. Click **Add Employee** → enter their name + email.
3. Send them the one-line installer command + their email.
4. They run it. Agent registers, dashboard shows them within 60 seconds.

If you want self-signup (anyone can install without admin pre-creating their
account), add a `register_user` endpoint that combines `users` + `agents/register`
in one call.

---

## Phase 6 — Things that should change before scaling beyond ~50 users

These are nice-to-haves; not blockers for going live:

| Change | Why | Priority |
|---|---|---|
| **Move screenshots to S3** | Local-disk fills up Railway's volume fast | Medium |
| **Add rate limiting per device** | Already coded — enable middleware | Medium |
| **Add backup job for PostgreSQL** | Railway has paid daily backups; or pg_dump cron to S3 | High |
| **Add Sentry / error tracking** | Catch bugs you'd otherwise miss | Medium |
| **Force password change on first login** | `admin123` → strong password | High |
| **Add 2FA for admin** | If you handle real PII | Medium |
| **GDPR data export endpoint** | Already exists in `gdpr.py` — wire to dashboard | Low |
| **Move agent download URL to CDN** | Faster install for global users | Low |

---

## How agents behave once you've gone live

After deploying:

- ✅ Developer in **India** at `172.16.100.32` → installer downloads from `https://trackme-production.up.railway.app` → agent registers → works.
- ✅ User on **office Wi-Fi** → same installer, same backend, just works.
- ✅ User on **home Wi-Fi**, **VPN**, **cafe**, **hotel** → still works. The agent's `api_base_url` is a stable HTTPS URL — no LAN dependency.
- ✅ Your laptop **off** → backend still up (it's on Railway, not your laptop).
- ✅ Your IP changes (you switch Wi-Fi) → no impact, agents talk to Railway.

---

## What to do RIGHT NOW

1. Push your repo to GitHub (15 min if not already done).
2. Sign up for Railway, deploy (10 min).
3. Add Postgres plugin (1 min).
4. Set env vars (`SECRET_KEY`, `CORS_ORIGINS`) (2 min).
5. Generate domain (instant).
6. Run seed (1 min).
7. Deploy dashboard to Vercel (5 min).
8. Update `DEFAULT_CONFIG` URLs in agent files (already done).
9. Update installer scripts to use new URL.
10. Test: install agent on any random machine — it should appear in the
    dashboard within 60 seconds without VPN/LAN.

**Total time: ~45 minutes.** After that, you can hand the installer URL to
anyone in the world.

---

## Cost breakdown

| Service | Free tier | After free tier |
|---|---|---|
| Railway backend | $5 credit/mo | ~$5–8/mo for small org |
| Railway PostgreSQL | included in same $5 | included |
| Vercel dashboard | unlimited bandwidth | $0 unless huge traffic |
| Domain (optional) | n/a | ~$10/year |
| Cloudflare R2 (if using S3) | 10 GB free | $0.015/GB after |

Realistic monthly cost for first 50 users: **$0**. For 200 users: **$10–15**.

---

## Rollback plan

If Railway breaks something:
- Railway keeps every deploy → click any past deployment → "Redeploy" reverts in 30 s.
- DB has automated backups (paid feature) — restore from any snapshot.

If you ever leave Railway:
- The Dockerfile + docker-compose.yml work on any cloud (DigitalOcean, AWS Lightsail, Hetzner). Migrate by exporting the Postgres dump.
