#!/usr/bin/env python3
"""
เก็บข้อมูล TikTok ของร้านคู่แข่งที่ใส่ลิงก์ไว้ใน She & Normal dashboard
อ่านรายชื่อร้าน+ลิงก์จาก Supabase (public.intel_competitors) แล้วเปิดแต่ละหน้าโปรไฟล์
TikTok ด้วย Playwright ดึงยอดผู้ติดตาม/ไลก์ + คลิปล่าสุดพร้อมยอดวิว แล้วเขียนกลับผ่าน
RPC (intel_save_channel_snapshot / intel_save_content) ที่มีรหัสหลังบ้านกันไว้ชั้นเดียวกับ
ที่หน้าเว็บใช้ — ไม่ต้องใช้ database password ตรง ๆ
"""
import asyncio, os, random, re, sys
import requests
from playwright.async_api import async_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# สคริปต์นี้แค่ "ทำตัวให้ดูเป็นเบราว์เซอร์คนใช้จริง" (ซ่อนสัญญาณอัตโนมัติมาตรฐาน + เลื่อนหน้าจอ/
# สุ่มดีเลย์แบบคน) เพื่อลดโอกาสถูกเรียก captcha ตั้งแต่แรก — ไม่ได้ไปแก้/ข้าม captcha ใด ๆ
# ถ้าเจอ captcha จริง สคริปต์จะแค่เก็บข้อมูลคลิปไม่ได้ (เหมือนเดิม) ไม่ได้พยายามฝ่าไป
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['th-TH', 'th', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""

# ========== CONFIG ==========
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rgfmwmypfgugtxofnydk.supabase.co")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]   # anon/publishable key เอง ไม่ใช่ secret จริง
ADMIN_KEY = os.environ["INTEL_ADMIN_KEY"]              # รหัสหลังบ้าน — ต้องมาจาก GitHub Secret เท่านั้น
MAX_VIDEOS_PER_SHOP = 8
LINE_TOKEN = os.environ.get("LINE_TOKEN")               # ถ้าไม่ตั้งจะข้ามการแจ้งเตือน LINE
LINE_TARGETS = [t for t in os.environ.get("LINE_TARGETS", "").split(",") if t]
# ============================

HEADERS = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}", "Content-Type": "application/json"}


def parse_count(text):
    """แปลง '1.2M' / '69.4K' / '595' เป็นตัวเลข"""
    if not text:
        return None
    text = text.strip().upper()
    m = re.match(r"^([\d.]+)([KMB]?)$", text)
    if not m:
        return None
    num, suffix = m.groups()
    num = float(num)
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(num * mult)


def get_competitors():
    r = requests.get(f"{SUPABASE_URL}/rest/v1/intel_competitors", headers=HEADERS,
                      params={"select": "id,name,active,channels"})
    r.raise_for_status()
    rows = r.json()
    out = []
    for c in rows:
        if not c.get("active"):
            continue
        for ch in c.get("channels") or []:
            if ch.get("channel") == "tiktok" and ch.get("url"):
                out.append({"id": c["id"], "name": c["name"], "url": ch["url"]})
    return out


def call_rpc(fn, payload):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"{fn} failed ({r.status_code}): {r.text}")


async def scrape_shop(page, shop):
    print(f"→ {shop['name']} ({shop['url']})")
    await page.goto(shop["url"], wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(random.uniform(2000, 3500))

    async def text_of(testid):
        el = await page.query_selector(f'[data-e2e="{testid}"]')
        return (await el.inner_text()) if el else None

    followers = parse_count(await text_of("followers-count"))
    likes = parse_count(await text_of("likes-count"))

    # เลื่อนหน้าจอแบบคน (ทีละนิด ไม่ใช่กระโดดทีเดียว) ก่อนอ่านรายการคลิป —
    # เผื่อกริดคลิปโหลดแบบ lazy และเพื่อให้พฤติกรรมดูเป็นคนมากขึ้น
    for _ in range(3):
        await page.mouse.wheel(0, random.randint(400, 900))
        await page.wait_for_timeout(random.uniform(400, 900))

    videos = await page.eval_on_selector_all(
        'a[href*="/video/"]',
        """(anchors) => anchors.map(a => {
            const box = a.closest('div');
            return { href: a.href, text: box ? box.textContent : '' };
        })""",
    )
    seen = set()
    video_rows = []
    for v in videos:
        href = v["href"]
        if href in seen:
            continue
        seen.add(href)
        vid = href.rstrip("/").split("/video/")[-1]
        views = parse_count(v["text"])
        video_rows.append({"external_id": vid, "url": href, "views": views})
        if len(video_rows) >= MAX_VIDEOS_PER_SHOP:
            break

    return {"followers": followers, "likes": likes, "videos": video_rows}


async def main():
    shops = get_competitors()
    if not shops:
        print("ไม่มีร้านที่ใส่ลิงก์ TikTok ไว้เลย — ไม่มีอะไรให้เก็บ")
        return

    results, errors = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="th-TH",
            timezone_id="Asia/Bangkok",
            extra_http_headers={"Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7"},
        )
        await context.add_init_script(STEALTH_INIT_SCRIPT)
        page = await context.new_page()

        for i, shop in enumerate(shops):
            if i > 0:
                await page.wait_for_timeout(random.uniform(1500, 4000))  # เว้นจังหวะระหว่างร้าน ไม่รัวติดกัน
            try:
                data = await scrape_shop(page, shop)
                call_rpc("intel_save_channel_snapshot", {
                    "k": ADMIN_KEY, "p_competitor_id": shop["id"], "p_channel": "tiktok",
                    "p_followers": data["followers"], "p_extra": {"likes": data["likes"], "source": "tiktok_collector"},
                })
                for v in data["videos"]:
                    call_rpc("intel_save_content", {
                        "k": ADMIN_KEY, "p_competitor_id": shop["id"], "p_channel": "tiktok",
                        "p_external_id": v["external_id"], "p_url": v["url"], "p_views": v["views"],
                    })
                print(f"  ✅ เก็บสำเร็จ: followers={data['followers']} คลิป={len(data['videos'])}")
                results.append(f"{shop['name']}: {data['followers']:,} followers" if data["followers"] else f"{shop['name']}: -")
            except Exception as e:
                print(f"  ❌ เก็บไม่สำเร็จ: {e}")
                errors.append(f"{shop['name']}: {e}")

        await browser.close()

    if LINE_TOKEN and LINE_TARGETS:
        lines = ["📊 She & Normal — เก็บข้อมูล TikTok คู่แข่งเสร็จแล้ว", ""]
        lines += results
        if errors:
            lines += ["", "⚠️ เก็บไม่สำเร็จ:"] + errors
        message = "\n".join(lines)
        for target in LINE_TARGETS:
            requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
                json={"to": target, "messages": [{"type": "text", "text": message}]},
            )

    if errors and not results:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
