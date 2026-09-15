#!/usr/bin/env python3
"""
เก็บยอดขายรายสินค้าของ She จาก Zort API (Order/GetOrders) แล้วรวมยอดขายต่อวันต่อ SKU
เขียนกลับผ่าน RPC intel_save_own_product_sale (มีรหัสหลังบ้านกันไว้เหมือนสคริปต์อื่น)

ต้องมี env vars:
  ZORT_STORENAME, ZORT_APIKEY, ZORT_APISECRET  — จาก Zort (ความลับ ห้ามฝังในโค้ด)
  SUPABASE_ANON_KEY, INTEL_ADMIN_KEY
"""
import os, sys, requests
from collections import defaultdict
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rgfmwmypfgugtxofnydk.supabase.co")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
ADMIN_KEY = os.environ["INTEL_ADMIN_KEY"]

ZORT_STORENAME = os.environ["ZORT_STORENAME"]
ZORT_APIKEY = os.environ["ZORT_APIKEY"]
ZORT_APISECRET = os.environ["ZORT_APISECRET"]
LOOKBACK_DAYS = int(os.environ.get("SALES_LOOKBACK_DAYS", "30"))

BRAND = "she"  # สคริปต์นี้ดึงแค่ She (Zort) — Normal ใช้ StoreHub คนละสคริปต์

HEADERS = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}", "Content-Type": "application/json"}
ZORT_HEADERS = {"storename": ZORT_STORENAME, "apikey": ZORT_APIKEY, "apisecret": ZORT_APISECRET}


def call_rpc(fn, payload):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"{fn} failed ({r.status_code}): {r.text}")


def fetch_orders():
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = date.today().isoformat()
    orders, page = [], 1
    while True:
        r = requests.get(
            "https://open-api.zortout.com/v4/Order/GetOrders",
            headers=ZORT_HEADERS,
            params={"orderdateafter": since, "orderdatebefore": until, "limit": 500, "page": page},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"Zort API error ({r.status_code}): {r.text}")
        data = r.json()
        batch = data.get("orders") or data.get("data") or []
        if not batch:
            break
        orders.extend(batch)
        if len(batch) < 500:
            break
        page += 1
    return orders


def main():
    orders = fetch_orders()
    print(f"📦 ดึงออเดอร์ได้ {len(orders)} รายการ (ย้อนหลัง {LOOKBACK_DAYS} วัน)")

    # รวมยอดขายต่อวันต่อ sku+ชื่อสินค้า
    agg = defaultdict(lambda: {"quantity": 0, "revenue": 0.0})
    for o in orders:
        order_date = (o.get("orderdateString") or o.get("createdatetimeString") or "")[:10]
        if not order_date:
            continue
        for item in o.get("orderproducts") or o.get("OrderProduct") or o.get("orderProducts") or []:
            sku = item.get("sku") or ""
            name = item.get("name") or "ไม่ทราบชื่อสินค้า"
            qty = int(item.get("number") or 0)
            total = float(item.get("totalprice") or 0)
            key = (order_date, sku, name)
            agg[key]["quantity"] += qty
            agg[key]["revenue"] += total

    saved, errors = 0, []
    for (order_date, sku, name), v in agg.items():
        try:
            call_rpc("intel_save_own_product_sale", {
                "k": ADMIN_KEY, "p_brand": BRAND, "p_date": order_date,
                "p_sku": sku, "p_product_name": name,
                "p_quantity": v["quantity"], "p_revenue": v["revenue"],
            })
            saved += 1
        except Exception as e:
            errors.append(str(e))

    print(f"✅ บันทึก {saved} แถว (วัน x สินค้า)")
    if errors:
        print(f"❌ ผิดพลาด {len(errors)} รายการ ตัวอย่าง: {errors[0]}")
        if saved == 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
