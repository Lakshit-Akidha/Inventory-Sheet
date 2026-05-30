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

    def get_inventory_report(self):
        url = f"{self.config['api_domain']}/inventory/v1/reports/inventorysummary"
        all_details = []
        page = 1
        while True:
            params = {'organization_id': self.config['organization_id'], 'page': page}
            r = request_with_retry('get', url, headers=self.get_headers(), params=params)
            if r.status_code != 200:
                print(f"❌ HTTP {r.status_code}")
                break
            data = r.json()
            if data.get('code') != 0:
                print(f"❌ API error: {data.get('message')}")
                break
            inventory = data.get('inventory', [])
            if not inventory:
                break
            for record in inventory:
                all_details.extend(record.get('item_details', []))
            print(f"   Page {page}: {sum(len(r.get('item_details',[])) for r in inventory)} items")
            if not data.get('page_context', {}).get('has_more_page', False):
                break
            page += 1
        return all_details


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

    print("\nFetching inventory data...")
    report_data = zoho_client.get_inventory_report()
    if not report_data:
        print("❌ No data fetched")
        raise SystemExit(1)

    df = pd.DataFrame(report_data)
    print(f"Total records: {len(df)}")

    # Rename to match the original sheet format exactly
    df = df.rename(columns={
        'quantity_available': 'available_stock',
        'quantity_available_for_sale': 'actual_available_stock'
    })

    # Select and order columns to match original format:
    # sku | item_name | available_stock | actual_available_stock | reorder_level | sync_timestamp
    final_cols = ['sku', 'item_name', 'available_stock', 'actual_available_stock', 'reorder_level']
    df = df[[c for c in final_cols if c in df.columns]]

    # Filter: only rows where available_stock > 0
    df['available_stock'] = pd.to_numeric(df['available_stock'], errors='coerce').fillna(0)
    df = df[df['available_stock'] > 0]
    print(f"Records with available_stock > 0: {len(df)}")

    # Add IST timestamp
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
