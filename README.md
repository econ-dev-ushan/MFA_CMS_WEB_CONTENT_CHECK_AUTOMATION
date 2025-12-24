Below is a clean, portable repo setup you can push to Git so anyone can clone, log in via their own browser, and run the script.

## Recommended folder layout

```
cms-public-sync/
  src/
    sync_missing_articles.py
  .gitignore
  .env.example
  requirements.txt
  README.md
```

---

## `requirements.txt`

```txt
httpx==0.27.2
beautifulsoup4==4.12.3
playwright==1.47.0
```

After installing requirements, Playwright also needs browser binaries (README covers it).

---

## `.env.example`

```txt
CMS_BASE_URL=https://sample_cms_admin_url.net
PUBLIC_LIST_URL=https://mfa.gov.lk/en/category/media-releases/
STORAGE_STATE=cms_storage_state.json
OUT_CSV=missing_articles.csv
START_PAGE=1
END_PAGE=3
LIMIT_PER_PAGE=
```

---

## `.gitignore`

```gitignore
__pycache__/
*.pyc
.venv/
venv/
.env

cms_storage_state.json
missing_articles.csv
*.log
```

---

## `README.md`

# CMS vs Public Website Sync (Missing Articles)

This tool checks a public article listing (default: MFA media releases) and verifies whether each article title exists in the CMS admin content list. Any titles missing from the CMS are appended to a CSV report.

## What it does

- Scrapes public list pages and extracts:
  - title
  - public URL
  - public date label (if present)
- For each title, queries the CMS admin content view using:
  - `/en/admin/content?title=<TITLE>&type=All&status=All&langcode=All`
- Writes missing items to `missing_articles.csv`

## Requirements

- Python 3.10+ recommended
- A browser login session to the CMS
- CMS login URL

## Setup

### 1) Clone and create a virtual environment

```bash
git clone <your-repo-url>
cd cms-public-sync

python -m venv .venv
source .venv/bin/activate
````

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install
```
You might need to install this package in host OS, if an installation error occurred - 

```bash
sudo apt-get update
sudo apt-get install libevent-2.1-7t64
```

### 3) Configure environment variables (optional)

Copy `.env.example` to `.env` and adjust values if needed:

```bash
cp .env.example .env
```

You can also skip `.env` and pass flags directly.

Note: `.env` is ignored by git.

## Usage

The tool has two steps:

### Step A: Save your authenticated CMS session (one-time per user/machine)

This opens a real browser window so you can log in manually. After you finish logging in, the script saves the session cookies/local storage into `cms_storage_state.json`.

```bash
python src/sync_missing_articles.py --init-auth --cms-base-url https://sample_cms_admin_login_url.net
```

When the browser opens:

1. You will be redirected to the CMS (or login page)
2. Log in using credentials
3. Ensure you can access the admin content page after login
4. Return to the terminal and press Enter

This will create:

* `cms_storage_state.json`

Do not commit this file.

### Step B: Run the sync and generate the CSV

Example (pages 1 to 3):

```bash
python src/sync_missing_articles.py \
  --cms-base-url https://sample_cms_admin_base_url.net \
  --public-list-url https://mfa.gov.lk/en/category/media-releases/ \
  --start-page 1 \
  --end-page 3 \
  --out missing_articles.csv
```

Optional limit per page:

```bash
python src/sync_missing_articles.py \
  --cms-base-url https://sample_cms_admin_url.net \
  --start-page 1 \
  --end-page 2 \
  --limit-per-page 10
```

## Output

The CSV contains:

* `title`
* `public_url`
* `public_date`
* `checked_at`

Example:

```csv
title,public_url,public_date,checked_at
Some Title,https://...,December 10, 2025,2025-12-24T17:10:01
```

## Notes / Troubleshooting

### Session expired / not logged in

If the script throws an error like "login/session may be invalid", re-run init-auth:

```bash
python src/sync_missing_articles.py --init-auth --cms-base-url https://sample_cms_admin_url.net
```

### Headless mode

The sync runs headless by default (no visible browser). Authentication is captured using a visible browser during `--init-auth`.


## Security

* `cms_storage_state.json` contains session data (cookies/local storage).
* Do not commit or share it.
* Each user should generate their own session state by running `--init-auth`.

---
