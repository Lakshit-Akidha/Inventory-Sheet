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
        r = request_with_retry('post', url, params=params)
        if r.status_code == 200:
            self.access_token = r.json().get('access_token')
            if self.access_token:
                print("✅ Zoho access token refreshed successfully")
                return True
        print(f"❌ Token refresh failed: {r.text}")
        return False

    def get_headers(self):
        return {'Authorization': f'Zoho-oauthtoken {self.access_token}'}

    def get_all_pages(self, url, key, extra_params=None):
        all_data = []
        page = 1
        while True:
            params = {'organization_id': self.config['organization_id'], 'page': page}
            if extra_params:
                params.update(extra_params)
            r = request_with_retry('get', url, headers=self.get_headers(), params=params)
            if r.status_code != 200:
                print(f"❌ HTTP {r.status_code}: {r.text[:300]}")
                break
            data = r.json()
            if data.get('code') != 0:
                print(f"❌ API error {data.get('code')}: {data.get('message')}")
                break
            items = data.get(key, [])
            if not items:
                break
            all_data.extend(items)
            print(f"   Page {page}: {len(items)} records")
            if not data.get('page_context', {}).get('has_more_page', False):
                break
            page += 1
        return all_data

    def get_report_keys(self):
        """Fetch first page of reports/inventorysummary and print all keys."""
        url = f"{self.config['api_domain']}/inventory/v1/reports/inventorysummary"
        params = {'organization_id': self.config['organization_id'], 'page': 1}
        r = request_with_retry('get', url, headers=self.get_headers(), params=params)
        print(f"\n--- reports/inventorysummary response ---")
        print(f"Status: {r.status_code}")
        try:
            body = r.json()
            print(f"Top-level keys: {list(body.keys())}")
            for k, v in body.items():
                if isinstance(v, list) and v:
                    print(f"  '{k}' is a list with {len(v)} items. First item keys: {list(v[0].keys())}")
                    return k, v  # return key name and first page data
                elif isinstance(v, list):
                    print(f"  '{k}' is an empty list")
                else:
                    print(f"  '{k}': {str(v)[:100]}")
        except Exception as e:
            print(f"Could not parse JSON: {e}")
            print(r.text[:500])
        print("--- end ---\n")
        return None, []

    def get_items(self):
        url = f"{self.config['api_domain']}/inventory/v1/items"
        return self.get_all_pages(url, 'items')


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
            return False
        data = data.fillna('').replace([float('inf'), float('-inf')], '')
        data = data.astype(str).replace('nan', '').replace('None', '')
        values = [data.columns.tolist()] + data.values.tolist()
        result = self.service.spreadsheets().values().update(
            spreadsheetId=self.config['spreadsheet_id'],
            range=f"'{sheet_name}'!A1",
            valueInputOption='USER_ENTERED',
            body={'values': values}
        ).execute()
        print(f"✅ Updated {result.get('updatedCells', 0)} cells in '{sheet_name}'")
        return True


def main():
    print("=" * 60)
    print("ZOHO INVENTORY → GOOGLE SHEETS SYNC")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    zoho_client = ZohoInventoryClient(ZOHO_CONFIG)
    if not zoho_client.refresh_access_token():
        raise SystemExit(1)

    sheets_client = GoogleSheetsClient(GOOGLE_CONFIG)

    # Step 1: Inspect the reports endpoint to find the right key + columns
    report_key, _ = zoho_client.get_report_keys()

    # Step 2: Fetch items (always works) and merge with report data if available
    print("\nFetching items...")
    items_data = zoho_client.get_items()
    if not items_data:
        print("❌ No items fetched")
        raise SystemExit(1)

    df_items = pd.DataFrame(items_data)
    print(f"Items fetched: {len(df_items)}")

    # Step 3: If report endpoint has a valid key, fetch stock from there and merge
    if report_key:
        print(f"\nFetching report data (key='{report_key}')...")
        url = f"{zoho_client.config['api_domain']}/inventory/v1/reports/inventorysummary"
        report_data = zoho_client.get_all_pages(url, report_key)
        if report_data:
            df_report = pd.DataFrame(report_data)
            print(f"Report records: {len(df_report)}")
            print(f"Report columns: {list(df_report.columns)}")
            # Merge on item_id or sku
            merge_col = 'item_id' if 'item_id' in df_report.columns else 'sku'
            if merge_col in df_items.columns and merge_col in df_report.columns:
                stock_cols = [c for c in df_report.columns if any(x in c for x in
                    ['stock', 'available', 'committed', 'reorder'])]
                keep = [merge_col] + stock_cols
                df_report = df_report[[c for c in keep if c in df_report.columns]]
                df_items = df_items.merge(df_report, on=merge_col, how='left')
                print(f"Merged. Stock columns added: {stock_cols}")

    # Step 4: Select final columns
    priority = ['sku', 'item_name', 'stock_on_hand', 'available_stock',
                'actual_available_stock', 'available_for_sale',
                'committed_stock', 'reorder_level']
    cols = [c for c in priority if c in df_items.columns]
    print(f"\nFinal columns: {cols}")
    df = df_items[cols]

    # Filter by stock > 0
    stock_col = next((c for c in ['stock_on_hand', 'available_stock', 'available_for_sale']
                      if c in df.columns), None)
    if stock_col:
        df = df[pd.to_numeric(df[stock_col], errors='coerce').fillna(0) > 0]
        print(f"After filter ({stock_col} > 0): {len(df)} records")

    ist = timezone(timedelta(hours=5, minutes=30))
    df['sync_timestamp'] = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

    print("\nWriting to Google Sheets...")
    sheets_client.clear_sheet()
    success = sheets_client.update_sheet(df)

    print("\n" + "=" * 60)
    print(f"✅ SYNC COMPLETED — {len(df)} records" if success else "❌ SYNC FAILED")
    print("=" * 60)

if __name__ == "__main__":
    main()
