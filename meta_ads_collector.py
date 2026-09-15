#!/usr/bin/env python3
"""
เก็บข้อมูล Meta Ads (Facebook/Instagram) รายวันของ She และ Normal เอง
ดึงจาก Graph Marketing API (insights, daily breakdown) แล้วเขียนกลับผ่าน RPC
intel_save_own_ads_performance ที่มีรหัสหลังบ้านกันไว้เหมือนสคริปต์อื่น ๆ ในนี้

ต้องมี env vars:
  META_ACCESS_TOKEN   — token ของ System User (สิทธิ์ ads_read) — เป็นความลับ ห้ามฝังในโค้ด
  SUPABASE_ANON_KEY
  INTEL_ADMIN_KEY
"""
import os, sys, requests
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rgfmwmypfgugtxofnydk.supabase.co")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
ADMIN_KEY = os.environ["INTEL_ADMIN_KEY"]
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
GRAPH_VERSION = "v21.0"
LOOKBACK_DAYS = int(os.environ.get("ADS_LOOKBACK_DAYS", "14"))  # ย้อนหลังกี่วัน (เผื่อ Meta รายงานตัวเลขช้า)

# ad account id ต่อแบรนด์ — เก็บไว้ในโค้ดได้ เพราะเป็นแค่ตัวระบุบัญชี ไม่ใช่ความลับ
AD_ACCOUNTS = {
    "she": "279220986878357",
    "normal": "327519824556564",
}

HEADERS = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}", "Content-Type": "application/json"}


def call_rpc(fn, payload):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"{fn} failed ({r.status_code}): {r.text}")


def fetch_daily_insights(ad_account_id):
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = date.today().isoformat()
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/act_{ad_account_id}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "spend,impressions,clicks,reach,ctr,cpc",
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "time_increment": 1,
        "level": "account",
    }
    r = requests.get(url, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Graph API error ({r.status_code}): {r.text}")
    return r.json().get("data", [])


def main():
    results, errors = [], []
    for brand, acct_id in AD_ACCOUNTS.items():
        try:
            rows = fetch_daily_insights(acct_id)
            for row in rows:
                call_rpc("intel_save_own_ads_performance", {
                    "k": ADMIN_KEY,
                    "p_brand": brand,
                    "p_date": row.get("date_start"),
                    "p_spend": float(row["spend"]) if row.get("spend") else None,
                    "p_impressions": int(row["impressions"]) if row.get("impressions") else None,
                    "p_clicks": int(row["clicks"]) if row.get("clicks") else None,
                    "p_reach": int(row["reach"]) if row.get("reach") else None,
                    "p_ctr": float(row["ctr"]) if row.get("ctr") else None,
                    "p_cpc": float(row["cpc"]) if row.get("cpc") else None,
                })
            print(f"✅ {brand}: บันทึก {len(rows)} วัน")
            results.append(f"{brand}: {len(rows)} วัน")
        except Exception as e:
            print(f"❌ {brand}: เก็บไม่สำเร็จ — {e}")
            errors.append(f"{brand}: {e}")

    if errors and not results:
        sys.exit(1)


if __name__ == "__main__":
    main()
