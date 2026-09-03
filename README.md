# Shopify Profit Dashboard

Streamlit dashboard that pulls orders from Shopify, reads purchase costs from Google Sheets, and calculates profit per product.

## Setup

### 1. Shopify API

Your app needs `read_orders` scope. The dashboard uses client credentials to request a fresh token on each load.

- Client ID and Client Secret from [Dev Dashboard](https://dev.shopify.com/dashboard/)

### 2. Google Sheets

Create a Google Cloud service account with Sheets API + Drive API enabled. Share your spreadsheet with the service account email.

Steps:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create project → Enable "Google Sheets API" and "Google Drive API"
3. Create Service Account → Download JSON key
4. Share your spreadsheet with the service account email (viewer access)

### 3. Secrets

**Local development**: Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in values.

**Streamlit Cloud**: Add secrets via the app settings UI.

### 4. Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

### 5. Deploy to Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Add secrets in the app settings
5. Deploy

The app uses Google SSO — only authenticated users can access the dashboard.

### 6. Uptime monitoring for nariz.no

UptimeRobot runs independently from Streamlit, so its alerts work even when the
dashboard is closed or unavailable.

1. Create an account at [UptimeRobot](https://uptimerobot.com/).
2. Create an `HTTP(s)` monitor named `Nariz.no` for `https://nariz.no`, using a
   five-minute interval.
3. Add and confirm email alert contacts for the intended recipients, then attach
   both contacts to the monitor. UptimeRobot will notify them when the site goes
   down and when it recovers.
4. In UptimeRobot's **Integrations → API** area, create a **read-only API key**.
5. In Streamlit Community Cloud, open **Manage app → Settings → Secrets** and add:

   ```toml
   [uptimerobot]
   api_key = "your_read_only_uptimerobot_api_key"
   monitor_url = "https://nariz.no"
   ```

6. Save the secrets. The **🟢 Oppetid** dashboard tab will show monitor status,
   incident count, and downtime history.

Never commit the UptimeRobot API key to GitHub. A read-only key is sufficient;
do not use an account key that can modify monitors or alert contacts.

## Architecture

```
Google Sheets (cost data)  ──┐
                             ├──▶  Streamlit app  ──▶  Streamlit Cloud
Shopify API (orders)  ───────┘     (pandas join)       (Google SSO)

UptimeRobot ──▶ E-postvarsler
      │
      └──────────────▶ Streamlit Oppetid-fane
```

## Data Flow

1. Request fresh Shopify access token (expires 24h, auto-refreshed)
2. Fetch all orders with pagination
3. Read cost spreadsheet via Google Sheets API
4. Join on product name (title + variant)
5. Calculate: revenue, COGS, profit, margin %
6. Render interactive charts and tables
