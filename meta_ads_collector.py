#!/usr/bin/env python3
"""
เก็บข้อมูล Meta Ads (Facebook/Instagram) รายวันของ She และ Normal เอง — ทั้งระดับบัญชีรวม
(สำหรับหน้าสรุป) และระดับ "โฆษณาแต่ละตัว" พร้อมครีเอทีฟ (รูปพรีวิว/ข้อความ) สำหรับหน้าคลิกดูรายละเอียด
เขียนกลับผ่าน RPC ที่มีรหัสหลังบ้านกันไว้เหมือนสคริปต์อื่น ๆ ในนี้

ต้องมี env vars:
  META_ACCESS_TOKEN   — token ของ System User (สิทธิ์ ads_read) — เป็นความลับ ห้ามฝังในโค้ด
  INTEL_ADMIN_KEY — ใส่ Supabase secret key (sb_secret_...) — เป็นความลับ
"""
import os, sys, requests
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rgfmwmypfgugtxofnydk.supabase.co")
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
LOOKBACK_DAYS = int(os.environ.get("ADS_LOOKBACK_DAYS", "14"))  # ย้อนหลังกี่วัน (เผื่อ Meta รายงานตัวเลขช้า)

# ad account id ต่อแบรนด์ — เก็บไว้ในโค้ดได้ เพราะเป็นแค่ตัวระบุบัญชี ไม่ใช่ความลับ
AD_ACCOUNTS = {
    "she": "279220986878357",
    "normal": "327519824556564",
}

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


def call_rpc(fn, payload):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"{fn} failed ({r.status_code}): {r.text}")


def graph_get(path, params):
    params = {**params, "access_token": META_ACCESS_TOKEN}
    r = requests.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{path}", params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Graph API error on {path} ({r.status_code}): {r.text}")
    return r.json()


def fetch_account_daily(ad_account_id, since, until):
    data = graph_get(f"act_{ad_account_id}/insights", {
        "fields": "spend,impressions,clicks,reach,ctr,cpc",
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "time_increment": 1,
        "level": "account",
    })
    return data.get("data", [])


def fetch_ad_level_daily(ad_account_id, since, until):
    """คืนรายวันของแต่ละโฆษณา (ad_id) — ใช้สรุปยอดสะสมต่อ ad และรายวันแยกต่อ ad"""
    data = graph_get(f"act_{ad_account_id}/insights", {
        "fields": "ad_id,ad_name,campaign_id,campaign_name,adset_id,adset_name,objective,spend,impressions,clicks,reach,ctr,cpc,actions",
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "time_increment": 1,
        "level": "ad",
        "limit": 200,
    })
    rows = data.get("data", [])
    # ตาม paging.next ถ้ามี (เผื่อโฆษณาเยอะ)
    paging = data.get("paging", {})
    next_url = paging.get("next")
    while next_url:
        r = requests.get(next_url, timeout=30)
        if not r.ok:
            break
        page = r.json()
        rows.extend(page.get("data", []))
        next_url = page.get("paging", {}).get("next")
    return rows


def count_results(actions):
    """นับ 'ผลลัพธ์' จาก actions — เอา purchase/lead/onsite_conversion มารวมกันแบบคร่าว ๆ"""
    if not actions:
        return None
    interesting = {"purchase", "offsite_conversion.fb_pixel_purchase", "lead", "onsite_conversion.purchase"}
    total = sum(int(float(a.get("value", 0))) for a in actions if a.get("action_type") in interesting)
    return total or None


MESSAGE_ACTIONS = {"onsite_conversion.messaging_conversation_started_7d"}


def count_messages(actions):
    """จำนวนแชตที่เริ่มคุย (Messenger/IG/WhatsApp) — แคมเปญทักแชตวัดผลด้วยตัวนี้"""
    if not actions:
        return None
    total = sum(int(float(a.get("value", 0))) for a in actions if a.get("action_type") in MESSAGE_ACTIONS)
    return total or None


def fetch_ad_creative(ad_id):
    try:
        data = graph_get(ad_id, {"fields": "creative{thumbnail_url,title,body,image_url}"})
    except Exception:
        return {}
    creative = data.get("creative") or {}
    return {
        "thumbnail_url": creative.get("thumbnail_url") or creative.get("image_url"),
        "title": creative.get("title"),
        "body": creative.get("body"),
    }


def fetch_ad_status(ad_id):
    try:
        data = graph_get(ad_id, {"fields": "effective_status"})
        return data.get("effective_status")
    except Exception:
        return None


def main():
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = date.today().isoformat()
    results, errors = [], []

    for brand, acct_id in AD_ACCOUNTS.items():
        try:
            # 1) ยอดรวมระดับบัญชี (สำหรับหน้าสรุป)
            acct_rows = fetch_account_daily(acct_id, since, until)
            for row in acct_rows:
                call_rpc("intel_save_own_ads_performance", {
                    "k": ADMIN_KEY, "p_brand": brand, "p_date": row.get("date_start"),
                    "p_spend": float(row["spend"]) if row.get("spend") else None,
                    "p_impressions": int(row["impressions"]) if row.get("impressions") else None,
                    "p_clicks": int(row["clicks"]) if row.get("clicks") else None,
                    "p_reach": int(row["reach"]) if row.get("reach") else None,
                    "p_ctr": float(row["ctr"]) if row.get("ctr") else None,
                    "p_cpc": float(row["cpc"]) if row.get("cpc") else None,
                })
            print(f"✅ {brand}: บัญชีรวม {len(acct_rows)} วัน")

            # 2) ระดับโฆษณาแต่ละตัว
            ad_rows = fetch_ad_level_daily(acct_id, since, until)
            seen_ads = {}
            for row in ad_rows:
                ad_id = row.get("ad_id")
                if not ad_id:
                    continue
                seen_ads[ad_id] = {
                    "campaign_id": row.get("campaign_id"), "campaign_name": row.get("campaign_name"),
                    "adset_id": row.get("adset_id"), "adset_name": row.get("adset_name"), "ad_name": row.get("ad_name"),
                    "objective": row.get("objective"),
                }

            # ดึงครีเอทีฟ+สถานะของแต่ละโฆษณา (ครั้งเดียวต่อ ad ไม่ใช่ต่อวัน)
            for ad_id, meta in seen_ads.items():
                creative = fetch_ad_creative(ad_id)
                status = fetch_ad_status(ad_id)
                call_rpc("intel_save_own_ad", {
                    "k": ADMIN_KEY, "p_brand": brand, "p_ad_id": ad_id,
                    "p_campaign_id": meta["campaign_id"], "p_campaign_name": meta["campaign_name"],
                    "p_adset_name": meta["adset_name"], "p_ad_name": meta["ad_name"],
                    "p_thumbnail_url": creative.get("thumbnail_url"),
                    "p_title": creative.get("title"), "p_body": creative.get("body"),
                    "p_status": status,
                    "p_objective": meta["objective"], "p_adset_id": meta["adset_id"],
                })

            for row in ad_rows:
                ad_id = row.get("ad_id")
                if not ad_id:
                    continue
                results_count = count_results(row.get("actions"))
                spend = float(row["spend"]) if row.get("spend") else None
                cost_per_result = (spend / results_count) if (spend and results_count) else None
                call_rpc("intel_save_own_ad_performance", {
                    "k": ADMIN_KEY, "p_brand": brand, "p_ad_id": ad_id, "p_date": row.get("date_start"),
                    "p_spend": spend,
                    "p_impressions": int(row["impressions"]) if row.get("impressions") else None,
                    "p_clicks": int(row["clicks"]) if row.get("clicks") else None,
                    "p_reach": int(row["reach"]) if row.get("reach") else None,
                    "p_ctr": float(row["ctr"]) if row.get("ctr") else None,
                    "p_cpc": float(row["cpc"]) if row.get("cpc") else None,
                    "p_results": results_count, "p_cost_per_result": cost_per_result,
                    "p_messages": count_messages(row.get("actions")),
                })
            print(f"✅ {brand}: {len(seen_ads)} โฆษณา, {len(ad_rows)} แถวรายวัน")
            results.append(f"{brand}: {len(seen_ads)} ads")
        except Exception as e:
            print(f"❌ {brand}: เก็บไม่สำเร็จ — {e}")
            errors.append(f"{brand}: {e}")

    if errors and not results:
        sys.exit(1)


if __name__ == "__main__":
    main()
