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

## Architecture

```
Google Sheets (cost data)  ──┐
                             ├──▶  Streamlit app  ──▶  Streamlit Cloud
Shopify API (orders)  ───────┘     (pandas join)       (Google SSO)
```

## Data Flow

1. Request fresh Shopify access token (expires 24h, auto-refreshed)
2. Fetch all orders with pagination
3. Read cost spreadsheet via Google Sheets API
4. Join on product name (title + variant)
5. Calculate: revenue, COGS, profit, margin %
6. Render interactive charts and tables
