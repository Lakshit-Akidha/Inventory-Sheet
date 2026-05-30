# Zoho Inventory → Google Sheets Sync

Automatically syncs inventory data from Zoho Inventory to the
`Inventory_Stock` tab in Google Sheets, running every 2 hours via GitHub Actions.

---

## Files

| File | Purpose |
|------|---------|
| `sync.py` | Main sync script |
| `requirements.txt` | Python dependencies |
| `.github/workflows/sync.yml` | GitHub Actions schedule |

---

## One-Time Setup (15 minutes)

### Step 1 — Create a GitHub Repository

1. Go to [github.com](https://github.com) → **New repository**
2. Name it e.g. `zoho-sheets-sync`
3. Set it to **Private**
4. Click **Create repository**

### Step 2 — Upload the Files

Upload these 3 files maintaining the folder structure:
```
sync.py
requirements.txt
.github/workflows/sync.yml
```

You can do this via the GitHub web UI (drag & drop) or via git:
```bash
git init
git add .
git commit -m "Initial sync setup"
git remote add origin https://github.com/YOUR_ORG/zoho-sheets-sync.git
git push -u origin main
```

### Step 3 — Add Secrets to GitHub

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add each of these secrets:

| Secret Name | Value |
|-------------|-------|
| `ZOHO_CLIENT_ID` | `1000.0WR6E4Y03VHJ8Q55E6O0TD5UUGM0CB` |
| `ZOHO_CLIENT_SECRET` | `94f75f763ee0346aacd79ca7eb1e25c5e17d40215c` |
| `ZOHO_REFRESH_TOKEN` | `1000.299e32ed601cfe4fa787180ca678ca5a.94406934b9a915c69c1cf6a961fc9efa` |
| `ZOHO_ORG_ID` | `60044535547` |
| `GOOGLE_SPREADSHEET_ID` | `18SfYygPoOUM7gzhAmxzSusb7jbkzIEdFuwzISihx5dI` |
| `GOOGLE_SA_KEY` | *(paste the entire JSON from the service account key — the full `{...}` block)* |

### Step 4 — Test It Manually

1. In your repo, go to **Actions** tab
2. Click **Zoho Inventory → Google Sheets Sync**
3. Click **Run workflow** → **Run workflow**
4. Watch the logs — you should see ✅ messages and the sheet will update

---

## Schedule

The workflow runs **every 2 hours** by default (`0 */2 * * *`).

To change frequency, edit `.github/workflows/sync.yml` and update the cron line:

| Frequency | Cron |
|-----------|------|
| Every hour | `0 * * * *` |
| Every 2 hours | `0 */2 * * *` |
| Every 4 hours | `0 */4 * * *` |
| Twice a day (9am & 9pm IST = 3:30 & 15:30 UTC) | `30 3,15 * * *` |
| Once a day at 9am IST | `30 3 * * *` |

---

## Troubleshooting

**❌ Zoho authentication failed**
- The refresh token may have expired. Re-generate it from Zoho Developer Console.

**❌ Google Sheets auth failed**
- Confirm the service account `akidha-warehouse@akidha.iam.gserviceaccount.com` still has **Editor** access to the spreadsheet.
- Open the spreadsheet → Share → check the service account email is listed.

**⚠️ Some columns missing**
- The script will skip columns not returned by Zoho API and log a warning. The sync still completes.

**🚫 Rate limit hit**
- Zoho free/standard plans have API call limits. The script handles this gracefully and writes whatever was fetched before the limit was hit.
