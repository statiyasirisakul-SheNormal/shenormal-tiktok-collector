#!/usr/bin/env python3
"""
เก็บยอดขายของ She จาก Zort API (Order/GetOrders):
  1) รายสินค้าต่อวัน → RPC intel_save_own_product_sale
  2) รายได้จริงแยกช่องทาง + ต้นทุนสินค้า (ราคาทุนจาก Product/GetProducts) + ออเดอร์คืน/ติดแท็กโฆษณา → RPC intel_save_channel_sales

ต้องมี env vars:
  ZORT_STORENAME, ZORT_APIKEY, ZORT_APISECRET  — จาก Zort (ความลับ ห้ามฝังในโค้ด)
  INTEL_ADMIN_KEY — ใส่ Supabase secret key (sb_secret_...) — เป็นความลับ
"""
import os, re, sys, requests
from collections import defaultdict
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rgfmwmypfgugtxofnydk.supabase.co")

ZORT_STORENAME = os.environ["ZORT_STORENAME"]
ZORT_APIKEY = os.environ["ZORT_APIKEY"]
ZORT_APISECRET = os.environ["ZORT_APISECRET"]
LOOKBACK_DAYS = int(os.environ.get("SALES_LOOKBACK_DAYS", "30"))

BRAND = "she"  # สคริปต์นี้ดึงแค่ She (Zort) — Normal ใช้ StoreHub คนละสคริปต์

# สิทธิ์เขียน: ใช้ Supabase secret key (service_role) — ตั้งแต่ 15 ก.ย. 69 หลังบ้านเปลี่ยนไปเช็กผู้ใช้ที่ล็อกอิน
# รหัสหลังบ้าน (INTEL_ADMIN_KEY) ใช้เขียนไม่ได้แล้ว ตัวเก็บข้อมูลจึงต้องใช้ key นี้แทน — ห้ามฝังในโค้ด ต้องมาจาก GitHub Secret
# อ่านจาก SUPABASE_SERVICE_KEY ก่อน ถ้าไม่มีใช้ secret ชื่อเดิม INTEL_ADMIN_KEY (ให้ใส่ secret key ของ Supabase แทนรหัสหลังบ้าน —
# ไฟล์ workflow จะได้ไม่ต้องแก้)
SUPABASE_SERVICE_KEY = (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("INTEL_ADMIN_KEY") or "").strip()
if not SUPABASE_SERVICE_KEY.startswith(("sb_secret_", "eyJ")):
    sys.exit("❌ GitHub Secret INTEL_ADMIN_KEY ต้องเป็น Supabase secret key (ขึ้นต้น sb_secret_) — "
             "เอามาจาก Supabase › Project Settings › API Keys › Secret keys (รหัสหลังบ้านเดิมใช้เขียนข้อมูลไม่ได้แล้ว)")
ADMIN_KEY = ""  # ฟังก์ชันใน DB ยังรับพารามิเตอร์ k อยู่ แต่ไม่ได้ใช้ตรวจแล้ว
# key แบบใหม่ (sb_secret_...) ไม่ใช่ JWT → ส่งแค่ apikey; key แบบเก่า (JWT) ส่ง Authorization ด้วย
HEADERS = {"apikey": SUPABASE_SERVICE_KEY, "Content-Type": "application/json"}
if not SUPABASE_SERVICE_KEY.startswith("sb_"):
    HEADERS["Authorization"] = f"Bearer {SUPABASE_SERVICE_KEY}"
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
        batch = data.get("list") or []
        if not batch:
            break
        orders.extend(batch)
        if len(batch) < 500:
            break
        page += 1
    return orders


# ======================= ยอดขายจริงแยกช่องทาง + ต้นทุนสินค้า =======================
# เขียนลง intel.channel_sales (source = zort) ผ่าน RPC intel_save_channel_sales — หน้า "ค่าใช้จ่าย & กำไร" ใช้คิดกำไรจริง
SKIP_STATUS = {"voided"}                 # ออเดอร์ยกเลิก ไม่นับ
RETURN_STATUS = {"returned"}             # คืนสินค้า นับแยก ไม่รวมในยอดขาย
AD_TAG = re.compile(r"\bads?\b|โฆษณา|ยิงแอด", re.I)   # แท็กใน Zort ที่บอกว่าออเดอร์มาจากโฆษณา


def norm_channel(o):
    """ชื่อช่องทางจาก Zort → shopee / tiktok / lazada / facebook / instagram / line / store / other"""
    raw = " ".join(str(o.get(k) or "") for k in ("saleschannel", "integrationName")).lower()
    for key, pats in (("shopee", ("shopee",)), ("tiktok", ("tiktok", "tik tok")), ("lazada", ("lazada",)),
                      ("instagram", ("instagram", " ig")), ("facebook", ("facebook", "fb", "messenger", "เฟส")),
                      ("line", ("line", "ไลน์")), ("store", ("หน้าร้าน", "pos", "store", "walk"))):
        if any(p in " " + raw for p in pats):
            return key
    return "other"


def fetch_costs():
    """ราคาทุนต่อ SKU จาก Product/GetProducts (purchaseprice)"""
    costs, page = {}, 1
    while True:
        r = requests.get("https://open-api.zortout.com/v4/Product/GetProducts", headers=ZORT_HEADERS,
                         params={"limit": 500, "page": page}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"Zort GetProducts error ({r.status_code}): {r.text[:200]}")
        batch = r.json().get("list") or []
        for p in batch:
            try:
                price = float(p.get("purchaseprice") or 0)
            except ValueError:
                price = 0
            if p.get("sku") and price > 0:
                costs[p["sku"]] = price
        if len(batch) < 500:
            return costs
        page += 1


def channel_rows(orders, costs):
    agg = defaultdict(lambda: defaultdict(float))
    for o in orders:
        d = (o.get("orderdateString") or o.get("createdatetimeString") or "")[:10]
        status = str(o.get("status") or "").strip().lower()
        if not d or status in SKIP_STATUS:
            continue
        a = agg[(d, norm_channel(o))]
        net = float(o.get("amount") or 0) + float(o.get("voucheramount") or 0)
        if status in RETURN_STATUS:
            a["returned_orders"] += 1
            a["returned_amount"] += net
            continue
        a["orders"] += 1
        a["net"] += net
        a["discount"] += float(o.get("sellerdiscount") or 0)
        a["shipping_income"] += float(o.get("shippingamount") or 0)
        for it in o.get("list") or []:
            qty = float(it.get("number") or 0)
            a["items"] += qty
            cost = costs.get(it.get("sku") or "")
            if cost:
                a["cogs"] += qty * cost
            else:
                a["cogs_missing"] += qty
        if any(AD_TAG.search(str(t)) for t in (o.get("tag") or [])):
            a["ad_orders"] += 1
            a["ad_net"] += net
    rows = []
    for (d, ch), a in sorted(agg.items()):
        rows.append({"date": d, "channel": ch,
                     **{k: round(a[k], 2) for k in ("net", "discount", "shipping_income", "cogs", "returned_amount", "ad_net")},
                     **{k: int(a[k]) for k in ("orders", "items", "cogs_missing", "returned_orders", "ad_orders")}})
    return rows


def save_channel_sales(orders):
    try:
        costs = fetch_costs()
    except Exception as e:
        print(f"⚠️ ดึงราคาทุนไม่ได้ — บันทึกยอดขายโดยไม่มีต้นทุน: {e}")
        costs = {}
    rows = channel_rows(orders, costs)
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/intel_save_channel_sales", headers=HEADERS, timeout=60, json={
        "k": ADMIN_KEY, "p_brand": BRAND, "p_source": "zort", "p_rows": rows,
        "p_replace_from": since, "p_replace_to": date.today().isoformat()})
    if not r.ok:
        raise RuntimeError(f"intel_save_channel_sales failed ({r.status_code}): {r.text}")
    by_ch = defaultdict(float)
    for x in rows:
        by_ch[x["channel"]] += x["net"]
    print(f"✅ ยอดขายแยกช่องทาง {len(rows)} แถว · ราคาทุน {len(costs)} SKU · " +
          " · ".join(f"{k} ฿{v:,.0f}" for k, v in sorted(by_ch.items(), key=lambda kv: -kv[1])))


def main():
    orders = fetch_orders()
    print(f"📦 ดึงออเดอร์ได้ {len(orders)} รายการ (ย้อนหลัง {LOOKBACK_DAYS} วัน)")

    channel_error = None
    try:
        save_channel_sales(orders)
    except Exception as e:
        channel_error = e
        print(f"❌ บันทึกยอดขายแยกช่องทางไม่สำเร็จ: {e}")

    # รวมยอดขายต่อวันต่อ sku+ชื่อสินค้า
    agg = defaultdict(lambda: {"quantity": 0, "revenue": 0.0})
    for o in orders:
        order_date = (o.get("orderdateString") or o.get("createdatetimeString") or "")[:10]
        if not order_date:
            continue
        for item in o.get("list") or []:
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
    if channel_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
