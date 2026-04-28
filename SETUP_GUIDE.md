# TrackMe - Complete Setup & Usage Guide

## Prerequisites

Install these before starting:

| Tool | Version | Download |
|------|---------|----------|
| Docker Desktop | 4.x+ | https://www.docker.com/products/docker-desktop |
| Node.js | 18+ | https://nodejs.org |
| .NET SDK | 8.0 | https://dotnet.microsoft.com/download/dotnet/8.0 |
| Git | 2.x+ | https://git-scm.com |

---

## Step 1: Start Infrastructure Services

Open a terminal in the `TRACKER.AI` folder:

```bash
# Start PostgreSQL, Redis, and MinIO (S3-compatible storage)
docker compose up -d postgres redis minio minio-init
```

Wait 15 seconds for services to initialize, then verify:

```bash
docker compose ps
```

All services should show `healthy` or `running`.

---

## Step 2: Configure the Backend

```bash
# Navigate to the backend folder
cd backend

# Create environment file
cp .env.example .env
```

Edit `.env` and set a secure secret key:

```
SECRET_KEY=your-random-32-character-hex-string
DATABASE_URL=postgresql+asyncpg://trackme:trackme_secret@localhost:5432/trackme
```

---

## Step 3: Install Backend Dependencies

```bash
# Still in the backend/ folder
pip install -r requirements.txt
```

---

## Step 4: Initialize the Database

```bash
# Create tables and admin user
python seed_init.py
```

You should see:

```
[OK] Database tables created.
[OK] Organization created: My Organization
[OK] Admin user created

  Login credentials:
    Email:    admin@trackme.com
    Password: admin123
```

---

## Step 5: Start the Backend API

```bash
# Start the FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify it's running: open http://localhost:8000/api/v1/health/live in your browser.

---

## Step 6: Start the Dashboard

Open a NEW terminal:

```bash
# Navigate to the dashboard folder
cd dashboard

# Install dependencies (first time only)
npm install

# Start the dev server
npm run dev
```

The dashboard will be available at: **http://localhost:5173**

---

## Step 7: Login to the Dashboard

1. Open **http://localhost:5173** in your browser
2. Login with:
   - **Email:** `admin@trackme.com`
   - **Password:** `admin123`
3. You'll land on the **Admin Overview** page

---

## Step 8: Add Employees

1. On the Admin Overview page, click the **"Add Employee"** button (top right)
2. Fill in the form:
   - **Full Name:** Employee's name
   - **Email:** Employee's email address
   - **Role:** `viewer` (employee), `manager`, or `admin`
   - **Password:** Initial password for the employee
3. Click **"Create Employee"**
4. The employee will appear in the team list

Repeat for each employee you want to monitor.

---

## Step 9: Download & Deploy the Agent

### Option A: Download from Dashboard

1. On the Admin Overview page, click **"Download Agent"**
2. This downloads the `TrackMe Agent Installer` (`.exe` or `.msi`)
3. Send the installer to the employee's Windows PC

### Option B: Build the Agent from Source

On a machine with .NET 8 SDK:

```bash
cd agent/src/TrackMe.Agent

# Build self-contained single-file executable
dotnet publish -c Release -r win-x64 --self-contained true /p:PublishSingleFile=true -o ./publish
```

The agent executable will be at: `agent/src/TrackMe.Agent/publish/TrackMe.Agent.exe`

### Option C: Build with Installer (requires Inno Setup 6)

```powershell
cd agent
.\build.ps1 -CreateInstaller
```

This produces an MSI/EXE installer in `agent/installer/Output/`.

---

## Step 10: Configure the Agent on Employee PCs

Before running the agent, create the config file on each employee PC:

1. Create folder: `C:\ProgramData\TrackMe\`
2. Create file: `C:\ProgramData\TrackMe\config.json`

```json
{
  "agent_id": "",
  "api_base_url": "http://YOUR_SERVER_IP:8000/api/v1",
  "api_key": "",
  "idle_threshold_seconds": 60,
  "screenshot_interval_minutes": 5,
  "screenshot_quality": 60,
  "sync_interval_seconds": 60,
  "capture_all_monitors": false,
  "enabled_features": {
    "activity_tracking": true,
    "app_monitoring": true,
    "url_tracking": true,
    "screenshots": true
  },
  "log_level": "Information"
}
```

Replace `YOUR_SERVER_IP` with the IP address of the server running the backend.

The `agent_id` and `api_key` fields will be auto-populated when the agent registers with the backend on first run.

---

## Step 11: Run the Agent

### Run as Application (Testing)

```
TrackMe.Agent.exe
```

### Install as Windows Service (Production)

Run from an elevated (Administrator) command prompt:

```bash
sc create TrackMeAgent binPath="C:\path\to\TrackMe.Agent.exe" start=delayed-auto
sc start TrackMeAgent
```

Or use the installer (Step 9, Option C) which does this automatically.

---

## Step 12: Verify Data is Flowing

1. Go back to the **Dashboard** (http://localhost:5173)
2. Navigate to **Monitoring > Device Status** - you should see the agent(s) appear as "Online"
3. Navigate to **Admin Overview** - stats will update as data flows in
4. Navigate to **Monitoring > Screenshots** - screenshots will appear after the configured interval

---

## Using the Dashboard

### Admin Overview (`/dashboard/overview`)
- See total agents, online count, alerts, and avg productivity
- Activity heatmap, app usage, and top domains charts
- Add employees and download agent installers

### Team Dashboard (`/team/all/dashboard`)
- View all team members with productivity scores
- Filter by department
- Productivity trends and team comparisons

### Individual User Detail (`/user/{id}/detail`)
- Click on any team member to see their detailed activity
- Activity timeline, app usage breakdown, browsing history
- Productivity score gauge

### Monitoring > Screenshots (`/monitoring/screenshots`)
- Gallery view of captured screenshots
- Click to enlarge, navigate with arrow keys
- Filter by employee and date range

### Monitoring > Alerts (`/monitoring/alerts`)
- View system alerts (offline agents, unusual activity)
- Filter by severity, acknowledge/resolve alerts

### Monitoring > Device Status (`/monitoring/devices`)
- Real-time status of all installed agents
- CPU, memory, bandwidth usage per device

### Reports (`/reporting/reports`)
- Generate summary reports (PDF/HTML)
- Schedule recurring reports
- Download/view generated reports

### Settings (`/settings`)
- Organization settings
- Notification preferences
- Monitoring configuration
- Security settings

---

## Architecture Overview

```
Employee PC                   Your Server
+------------------+         +------------------+
| TrackMe Agent    |  REST   | FastAPI Backend   |
| (C# Windows Svc) | ------> | (Python 3.12)    |
|                  |  gzip   |                   |
| Tracks:          |         | PostgreSQL 16     |
|  - Mouse/Keyboard|         | Redis 7           |
|  - Active App    |         | MinIO (S3)        |
|  - Browser URLs  |         +------------------+
|  - Screenshots   |                |
+------------------+         +------------------+
                             | React Dashboard   |
                             | (Vite + TS)       |
                             | localhost:5173     |
                             +------------------+
```

---

## Troubleshooting

### Backend won't start
- Check PostgreSQL is running: `docker compose ps postgres`
- Check `.env` file has correct `DATABASE_URL`
- Run `python seed_init.py` to create tables

### Dashboard shows "Unable to load data"
- Ensure backend is running on port 8000
- Check browser console for CORS errors
- Verify `CORS_ORIGINS` in `.env` includes `http://localhost:5173`

### Agent can't connect
- Verify `api_base_url` in config.json points to the correct server
- Ensure port 8000 is accessible from the employee's PC
- Check the agent logs at `C:\ProgramData\TrackMe\logs\`

### No screenshots appearing
- Screenshots are captured every 5 minutes by default
- Verify MinIO is running: `docker compose ps minio`
- Check that `enabled_features.screenshots` is `true` in config.json

---

## Stopping Everything

```bash
# Stop infrastructure
docker compose down

# Stop backend: Ctrl+C in the terminal running uvicorn
# Stop dashboard: Ctrl+C in the terminal running npm run dev
```

To completely reset (delete all data):

```bash
docker compose down -v   # -v removes volumes (deletes all data)
```
