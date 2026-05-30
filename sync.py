import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ============================================================
# CONFIGURATION
# ============================================================

ZOHO_CONFIG = {
    'client_id': os.environ['ZOHO_CLIENT_ID'],
    'client_secret': os.environ['ZOHO_CLIENT_SECRET'],
    'refresh_token': os.environ['ZOHO_REFRESH_TOKEN'],
    'organization_id': os.environ['ZOHO_ORG_ID'],
    'api_domain': 'https://www.zohoapis.in',
    'accounts_domain': 'https://accounts.zoho.in'
}

# Support both file-based and env-var-based Google credentials
_sa_key_env = os.environ.get('GOOGLE_SA_KEY')
if _sa_key_env:
    _sa_info = json.loads(_sa_key_env)
    GOOGLE_CONFIG = {
        'credentials_info': _sa_info,  # dict, used directly
        'credentials_file': None,
        'spreadsheet_id': os.environ['GOOGLE_SPREADSHEET_ID'],
        'sheet_name': 'inventory-stock'
    }
else:
    GOOGLE_CONFIG = {
        'credentials_info': None,
        'credentials_file': os.environ.get('GOOGLE_CREDENTIALS_FILE', 'google_sa.json'),
        'spreadsheet_id': os.environ['GOOGLE_SPREADSHEET_ID'],
        'sheet_name': 'Inventory_Stock'
    }

EXPORT_COLUMNS = [
    'sku', 'item_name',
    'stock_on_hand', 'available_stock', 'actual_available_stock', 'reorder_level'
]

MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
RETRY_DELAY = 15

# ============================================================
# RETRY HELPER
# ============================================================

def request_with_retry(method, url, retries=MAX_RETRIES, **kwargs):
    kwargs.setdefault('timeout', REQUEST_TIMEOUT)
    for attempt in range(1, retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"⚠️  {method.upper()} {url} — Attempt {attempt}/{retries} failed: {e}")
            if attempt == retries:
                raise
            print(f"   Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

# ============================================================
# ZOHO INVENTORY CLIENT
# ============================================================

class ZohoInventoryClient:
    def __init__(self, config):
        self.config = config
        self.access_token = None

    def refresh_access_token(self):
        url = f"{self.config['accounts_domain']}/oauth/v2/token"
        params = {
            'refresh_token': self.config['refresh_token'],
            'client_id': self.config['client_id'],
            'client_secret': self.config['client_secret'],
            'grant_type': 'refresh_token'
        }
        response = request_with_retry('post', url, params=params)
        if response.status_code == 200:
            data = response.json()
            self.access_token = data.get('access_token')
            if self.access_token:
                print("✅ Zoho access token refreshed successfully")
                return True
            else:
                print(f"❌ No access_token in response: {data}")
                return False
        else:
            print(f"❌ Failed to refresh token: {response.status_code} - {response.text}")
            return False

    def get_headers(self):
        return {
            'Authorization': f'Zoho-oauthtoken {self.access_token}',
            'Content-Type': 'application/json'
        }

    def get_items(self):
        url = f"{self.config['api_domain']}/inventory/v1/items"
        all_data = []
        page = 1
        while True:
            params = {
                'organization_id': self.config['organization_id'],
                'page': page
            }
            response = request_with_retry('get', url, headers=self.get_headers(), params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    items = data.get('items', [])
                    if not items:
                        break
                    all_data.extend(items)
                    print(f"   Page {page}: {len(items)} items fetched")
                    page_context = data.get('page_context', {})
                    if not page_context.get('has_more_page', False):
                        break
                    page += 1
                elif data.get('code') == 45:
                    print(f"🚫 Zoho rate limit hit on page {page}. Quota exhausted for today.")
                    print(f"   Returning {len(all_data)} items fetched so far.")
                    break
                else:
                    print(f"❌ API Error: {data.get('message')}")
                    break
            elif response.status_code == 429:
                print(f"🚫 HTTP 429 on page {page}. Quota exhausted.")
                print(f"   Returning {len(all_data)} items fetched so far.")
                break
            else:
                print(f"❌ Request failed: {response.status_code} - {response.text}")
                break
        return all_data

# ============================================================
# GOOGLE SHEETS CLIENT
# ============================================================

class GoogleSheetsClient:
    def __init__(self, config):
        self.config = config
        self.service = None
        self._authenticate()

    def _authenticate(self):
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        if self.config.get('credentials_info'):
            creds = Credentials.from_service_account_info(
                self.config['credentials_info'],
                scopes=scopes
            )
        else:
            creds = Credentials.from_service_account_file(
                self.config['credentials_file'],
                scopes=scopes
            )
        self.service = build('sheets', 'v4', credentials=creds)
        print("✅ Google Sheets authenticated successfully")

    def clear_sheet(self, sheet_name=None):
        sheet_name = sheet_name or self.config['sheet_name']
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.config['spreadsheet_id'],
            range=f"{sheet_name}!A:ZZ"
        ).execute()
        print(f"   Sheet '{sheet_name}' cleared")

    def update_sheet(self, data, sheet_name=None):
        sheet_name = sheet_name or self.config['sheet_name']
        if isinstance(data, pd.DataFrame):
            data = data.astype(str)
            values = [data.columns.tolist()] + data.values.tolist()
        else:
            print("Data must be a DataFrame")
            return False
        body = {'values': values}
        result = self.service.spreadsheets().values().update(
            spreadsheetId=self.config['spreadsheet_id'],
            range=f"{sheet_name}!A1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        updated_cells = result.get('updatedCells', 0)
        print(f"✅ Updated {updated_cells} cells in '{sheet_name}'")
        return True

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("ZOHO INVENTORY → GOOGLE SHEETS SYNC")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # --- Zoho ---
    zoho_client = ZohoInventoryClient(ZOHO_CONFIG)
    if not zoho_client.refresh_access_token():
        print("❌ Zoho authentication failed. Exiting.")
        raise SystemExit(1)

    # --- Google Sheets ---
    sheets_client = GoogleSheetsClient(GOOGLE_CONFIG)

    # --- Fetch data ---
    print("\nFetching items from Zoho Inventory...")
    data = zoho_client.get_items()
    if not data:
        print("❌ No data fetched from Zoho")
        raise SystemExit(1)

    df = pd.DataFrame(data)
    print(f"\nTotal records fetched: {len(df)}")

    # Keep only the columns we need (gracefully handle missing ones)
    available_cols = [c for c in EXPORT_COLUMNS if c in df.columns]
    missing_cols = [c for c in EXPORT_COLUMNS if c not in df.columns]
    if missing_cols:
        print(f"⚠️  Columns not found in API response (will be skipped): {missing_cols}")
    df = df[available_cols]

    # Filter to only rows with available stock > 0
    if 'available_stock' in df.columns:
        df = df[pd.to_numeric(df['available_stock'], errors='coerce').fillna(0) > 0]
        print(f"Records with available_stock > 0: {len(df)}")

    # Add IST timestamp
    ist = timezone(timedelta(hours=5, minutes=30))
    df['sync_timestamp'] = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

    # --- Write to Sheets ---
    print("\nWriting to Google Sheets...")
    sheets_client.clear_sheet()
    success = sheets_client.update_sheet(df)

    print("\n" + "=" * 60)
    if success:
        print(f"✅ SYNC COMPLETED — {len(df)} records written")
    else:
        print("❌ SYNC FAILED")
        raise SystemExit(1)
    print("=" * 60)

if __name__ == "__main__":
    main()
