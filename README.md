# DGEF Nationalité Watcher

Monitors your **Demande d'accès à la Nationalité Française** tab on the DGEF portal once a day and emails you a screenshot the moment anything changes.

---

## How it works

```
Daily trigger
    └─► headless Chrome logs into your DGEF account
            └─► navigates to "Mes demandes" → clicks the Nationalité tab
                    └─► grabs the text content and hashes it
                            └─► compares to yesterday's hash (stored in state.json)
                                    └─► if different → screenshot + email alert
```

---

## Quick start (local / your own computer)

### 1. Prerequisites

- Python 3.10+
- Google Chrome installed

### 2. Install

```bash
git clone https://github.com/YOUR_USERNAME/dgef-watcher.git
cd dgef-watcher
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your credentials (see notes below)
```

### 4. Run once to record baseline

```bash
python run.py
```

On the **first run** no email is sent — it just records the current state of the page as the baseline for future comparisons.

### 5. Schedule daily (Linux/macOS cron)

```bash
crontab -e
```

Add a line like this (runs every day at 08:00):

```
0 8 * * * cd /path/to/dgef-watcher && python run.py >> watcher.log 2>&1
```

### 5b. Schedule daily (Windows Task Scheduler)

Create a task that runs:
```
python C:\path\to\dgef-watcher\run.py
```
Trigger: Daily at 08:00.

---

## Recommended: run for free on GitHub Actions

This is the cleanest option — no computer needs to be on, GitHub runs it for free, and you get full logs for every run.

### Step 1 — Create a Gmail App Password

You need this before anything else. You cannot use your regular Gmail password with SMTP.

1. Make sure 2-Step Verification is enabled on your Google account — go to https://myaccount.google.com/security and check under "How you sign in to Google"
2. Go to https://myaccount.google.com/apppasswords
3. In the "App name" field type `dgef-watcher` and click **Create**
4. Google will show you a 16-character password like `abcd efgh ijkl mnop` — **copy it now**, it won't be shown again
5. Keep it somewhere safe for the next steps

### Step 2 — Create a private GitHub repository

1. Go to https://github.com/new
2. Give it a name, e.g. `dgef-watcher`
3. Set visibility to **Private** — this is important since your credentials will live here
4. Leave everything else unchecked (no README, no .gitignore) — click **Create repository**
5. GitHub will show you a page with setup instructions — leave it open, you'll need the repo URL in the next step

### Step 3 — Push the code

Open a terminal in your project folder (`C:\Users\antho\Documents\NationalityApp`) and run:

```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/dgef-watcher.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your actual GitHub username. If prompted for credentials, use your GitHub username and a Personal Access Token (not your GitHub password — see https://github.com/settings/tokens if needed, create one with `repo` scope).

After this, refresh your GitHub repo page — you should see all your files there.

### Step 4 — Add your credentials as Secrets

GitHub Secrets are encrypted and never visible after you save them — even to you.

1. In your repo on GitHub, click **Settings** (top tab bar)
2. In the left sidebar, click **Secrets and variables** → **Actions**
3. Click **New repository secret** and add each of the following one by one:

| Secret name | What to put |
|---|---|
| `DGEF_EMAIL` | Your DGEF login email (e.g. `anthony.mucia1@gmail.com`) |
| `DGEF_PASSWORD` | Your DGEF account password |
| `NOTIFY_EMAIL_FROM` | The Gmail address that will send alerts (e.g. `anthony.mucia1@gmail.com`) |
| `NOTIFY_EMAIL_TO` | The address that receives alerts — can be same Gmail, or any email |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Same as `NOTIFY_EMAIL_FROM` |
| `SMTP_PASSWORD` | The 16-character App Password from Step 1 (no spaces) |

After adding all 8, the Secrets page should list them all by name (values are hidden).

### Step 5 — Enable and test the workflow

1. Click the **Actions** tab in your repo
2. If you see a yellow banner saying "Workflows aren't being run on this forked repository" or similar, click **I understand my workflows, go ahead and enable them**
3. In the left sidebar you should see **DGEF Watcher** — click it
4. Click the **Run workflow** dropdown on the right → **Run workflow** → confirm

This triggers a manual run immediately. Click into the run to watch the live logs. A successful run looks like:

```
=== DGEF watcher starting ===
Navigating to login page …
Login submitted, waiting for dashboard …
Nationalité tab opened.
Content hash: abc123…
First run: baseline recorded. No alert sent.   ← on first run
=== Done ===
```

On the first run, no email is sent — it just records the baseline. Trigger it a second time manually to confirm the "No change detected" path works. After that, the schedule takes over.

### Step 6 — Verify the schedule

The workflow is scheduled to run daily at **07:00 UTC**, which is 08:00 Paris time in summer (CEST) and 08:00 in winter (CET) — close enough year-round.

One caveat: GitHub may delay scheduled workflows by up to 15-20 minutes during busy periods. If you need it at a very precise time, that's a limitation of the free tier.

To change the schedule, edit `.github/workflows/watcher.yml` and modify the cron line:
```yaml
- cron: "0 7 * * *"   # 07:00 UTC daily
```
Cron format is `minute hour day month weekday`. Use https://crontab.guru to build expressions.

### Viewing past runs and screenshots

- **Actions tab** → click any past run → see full logs
- Each run uploads screenshots as an **artifact** — click a run, scroll to the bottom, download the `screenshots-N` zip to see what the page looked like
- Artifacts are kept for 30 days

---

## Selector troubleshooting

The DGEF portal is a Vue.js SPA and its CSS classes may change over time. If the watcher fails to find elements:

1. Check `watcher.log` and the error screenshots (`login_error.png`, `tab_error.png`)
2. Open the portal in your browser → right-click the element → Inspect
3. Update the `SELECTORS` dict in `watcher.py` to match the live HTML

The most fragile selectors are typically:
- `nationalite_tab` — the clickable tab element
- `content_block` — the container whose text is hashed

The current selectors use broad XPath text matches and are designed to be resilient, but the portal may require adjustment.

---

## Files

| File | Purpose |
|---|---|
| `watcher.py` | Main logic |
| `run.py` | Loads `.env` then calls `watcher.py` |
| `requirements.txt` | Python dependencies |
| `.env.example` | Configuration template |
| `.github/workflows/watcher.yml` | GitHub Actions schedule |
| `state.json` | Auto-generated; stores last-seen hash |
| `screenshots/` | Auto-generated; saved screenshots |
| `watcher.log` | Auto-generated; execution log |

---

## Privacy note

Your credentials are only ever stored in your local `.env` file (never committed to git) or in GitHub's encrypted Secrets store. The repo should be set to **Private**.