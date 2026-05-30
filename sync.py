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

_sa_key_env = os.environ.get('GOOGLE_SA_KEY')
if _sa_key_env:
    _sa_info = json.loads(_sa_key_env)
    GOOGLE_CONFIG = {
        'credentials_info': _sa_info,
        'credentials_file': None,
        'spreadsheet_id': os.environ['GOOGLE_SPREADSHEET_ID'],
        'sheet_name': 'inventory-stock'
    }
else:
    GOOGLE_CONFIG = {
        'credentials_info': None,
        'credentials_file': os.environ.get('GOOGLE_CREDENTIALS_FILE', 'google_sa.json'),
        'spreadsheet_id': os.environ['GOOGLE_SPREADSHEET_ID'],
        'sheet_name': 'inventory-stock'
    }

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
            return requests.request(method, url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"  Attempt {attempt}/{retries} failed: {e}")
            if attempt == retries:
                raise
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
            print(f"❌ No access_token in response: {data}")
            return False
        print(f"❌ Failed to refresh token: {response.status_code} - {response.text}")
        return False

    def get_headers(self):
        return {
            'Authorization': f'Zoho-oauthtoken {self.access_token}',
            'Content-Type': 'application/json'
        }

    def get_all_pages(self, url, key):
        """Generic paginated fetcher."""
        all_data = []
        page = 1
        while True:
            params = {'organization_id': self.config['organization_id'], 'page': page}
            response = request_with_retry('get', url, headers=self.get_headers(), params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    items = data.get(key, [])
                    if not items:
                        break
                    all_data.extend(items)
                    print(f"   Page {page}: {len(items)} records")
                    if not data.get('page_context', {}).get('has_more_page', False):
                        break
                    page += 1
                elif data.get('code') == 45:
                    print(f"🚫 Rate limit. Returning {len(all_data)} records.")
                    break
                else:
                    print(f"❌ API Error: {data.get('message')}")
                    break
            elif response.status_code == 429:
                print(f"🚫 HTTP 429. Returning {len(all_data)} records.")
                break
            else:
                print(f"❌ Request failed: {response.status_code}")
                break
        return all_data

    def get_items(self):
        print("Fetching items...")
        url = f"{self.config['api_domain']}/inventory/v1/items"
        return self.get_all_pages(url, 'items')

    def get_inventory_summary(self):
        """Fetch inventory summary which includes stock quantities."""
        print("Fetching inventory summary (stock levels)...")
        url = f"{self.config['api_domain']}/inventory/v1/inventorysummary"
        return self.get_all_pages(url, 'inventory_summary')

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
            creds = Credentials.from_service_account_info(self.config['credentials_info'], scopes=scopes)
        else:
            creds = Credentials.from_service_account_file(self.config['credentials_file'], scopes=scopes)
        self.service = build('sheets', 'v4', credentials=creds)
        print("✅ Google Sheets authenticated successfully")

    def clear_sheet(self, sheet_name=None):
        sheet_name = sheet_name or self.config['sheet_name']
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.config['spreadsheet_id'],
            range=f"'{sheet_name}'!A:ZZ"
        ).execute()
        print(f"   Sheet '{sheet_name}' cleared")

    def update_sheet(self, data, sheet_name=None):
        sheet_name = sheet_name or self.config['sheet_name']
        if not isinstance(data, pd.DataFrame):
            print("Data must be a DataFrame")
            return False
        data = data.fillna('').replace([float('inf'), float('-inf')], '')
        data = data.astype(str).replace('nan', '').replace('None', '')
        values = [data.columns.tolist()] + data.values.tolist()
        body = {'values': values}
        result = self.service.spreadsheets().values().update(
            spreadsheetId=self.config['spreadsheet_id'],
            range=f"'{sheet_name}'!A1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        print(f"✅ Updated {result.get('updatedCells', 0)} cells in '{sheet_name}'")
        return True

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("ZOHO INVENTORY → GOOGLE SHEETS SYNC")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    zoho_client = ZohoInventoryClient(ZOHO_CONFIG)
    if not zoho_client.refresh_access_token():
        raise SystemExit(1)

    sheets_client = GoogleSheetsClient(GOOGLE_CONFIG)

    # Fetch inventory summary (has stock quantities)
    summary_data = zoho_client.get_inventory_summary()

    if summary_data:
        print(f"\nInventory summary records: {len(summary_data)}")
        df = pd.DataFrame(summary_data)
        print(f"Summary columns: {list(df.columns)}")

        # Keep useful columns
        keep = ['sku', 'item_name', 'item_id', 'stock_on_hand',
                'available_stock', 'actual_available_stock',
                'committed_stock', 'available_for_sale', 'reorder_level']
        cols = [c for c in keep if c in df.columns]
        df = df[cols]

        # Filter stock > 0
        stock_col = next((c for c in ['stock_on_hand', 'available_stock', 'available_for_sale'] if c in df.columns), None)
        if stock_col:
            df = df[pd.to_numeric(df[stock_col], errors='coerce').fillna(0) > 0]
            print(f"Records with {stock_col} > 0: {len(df)}")
    else:
        # Fallback: use items endpoint with all columns
        print("\n⚠️  No inventory summary data, falling back to items endpoint")
        items_data = zoho_client.get_items()
        if not items_data:
            print("❌ No data fetched")
            raise SystemExit(1)
        df = pd.DataFrame(items_data)
        keep = ['sku', 'item_name', 'reorder_level']
        df = df[[c for c in keep if c in df.columns]]

    ist = timezone(timedelta(hours=5, minutes=30))
    df['sync_timestamp'] = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

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
