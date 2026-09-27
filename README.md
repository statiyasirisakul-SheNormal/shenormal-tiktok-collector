# She & Normal — TikTok Competitor Collector

เก็บยอดผู้ติดตาม/ไลก์ + คลิปล่าสุด(พร้อมยอดวิว) ของร้านคู่แข่งที่ตั้งไว้ในหลังบ้านของ
[she-normal-dashboard](../she-normal-dashboard) โดยอัตโนมัติทุกสัปดาห์ แล้วเขียนกลับเข้า
Supabase (project `rgfmwmypfgugtxofnydk`, schema `intel`)

## วิธีทำงาน

1. อ่านรายชื่อร้าน + ลิงก์ TikTok จาก `public.intel_competitors` (REST, ใช้ anon key อ่านอย่างเดียว)
2. เปิดแต่ละลิงก์ด้วย Playwright (headless Chromium) ดึงยอดผู้ติดตาม/ไลก์ + คลิปล่าสุดสูงสุด 8 คลิป
3. เขียนกลับผ่าน RPC `intel_save_channel_snapshot` / `intel_save_content` (มีรหัสหลังบ้านกันไว้
   เหมือนตอนกดบันทึกที่หน้าเว็บ — ไม่ใช้ database password ตรง ๆ)
4. ถ้าตั้ง `LINE_TOKEN` + `LINE_TARGETS` ไว้ จะส่งสรุปเข้า LINE ให้ด้วย (ไม่บังคับ)

**หมายเหตุ:** Shopee ทำแบบนี้ไม่ได้ เพราะ Shopee บังคับล็อกอินก่อนดูร้าน ยังต้องดึงมือ

## ตั้งค่าก่อนใช้งาน (GitHub Secrets)

ไปที่ repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | ค่า | จำเป็น |
|---|---|---|
| `INTEL_ADMIN_KEY` | **Secret key ของ Supabase** (ขึ้นต้น `sb_secret_`, Project Settings → API Keys → Secret keys) — ชื่อ secret เดิมแต่ค่าไม่ใช่รหัสหลังบ้านแล้ว · ใช้ร่วมกันทุกตัวเก็บ | ใช่ |

> ตั้งแต่ 15 ก.ย. 69 หลังบ้านเปลี่ยนไปตรวจสิทธิ์จากผู้ใช้ที่ล็อกอิน รหัสหลังบ้าน (`INTEL_ADMIN_KEY`) จึงใช้เขียนข้อมูลไม่ได้แล้ว
> ตัวเก็บข้อมูลต้องใช้ secret key แทน — ใส่ลงใน secret ชื่อเดิม `INTEL_ADMIN_KEY` (ไฟล์ workflow ไม่ต้องแก้) · `SUPABASE_ANON_KEY` ไม่ได้ใช้แล้ว
| `LINE_TOKEN` | LINE Messaging API channel access token | ไม่ (ถ้าอยากได้สรุปเข้า LINE) |
| `LINE_TARGETS` | user/group id คั่นด้วย comma เช่น `U123,C456` | ไม่ |

## รันเอง / ทดสอบ

- กด "Run workflow" ที่แท็บ Actions ของ repo (ปุ่ม workflow_dispatch) เพื่อรันได้ทันทีโดยไม่ต้องรอตารางเวลา
- รันในเครื่องตัวเอง:
  ```bash
  pip install -r requirements.txt
  playwright install chromium
  INTEL_ADMIN_KEY=sb_secret_... python tiktok_collector.py
  ```

## ตารางเวลา

ตั้งไว้ที่ทุกวันอาทิตย์ 06:00 (เวลาไทย) ตรงกับค่าเริ่มต้นในหน้าหลังบ้าน › รอบเก็บข้อมูล —
ถ้าเปลี่ยนค่าที่หน้าเว็บ ต้องมาแก้ cron ใน `.github/workflows/tiktok_collector.yml` เองด้วย
(ไฟล์นี้ไม่ได้อ่านค่าตารางเวลาจาก Supabase อัตโนมัติ)

## ข้อจำกัดที่รู้อยู่แล้ว

- ดึงได้แค่ข้อมูลที่เห็นบนหน้าโปรไฟล์สาธารณะ (ไม่ต้องล็อกอิน) — ไม่มี insight เชิงลึกแบบที่
  เจ้าของบัญชีเห็นเอง (reach, demographic ฯลฯ)
- ถ้า TikTok เปลี่ยนโครงหน้าเว็บ (data-e2e attributes) สคริปต์อาจต้องแก้ selector ใหม่
- ยอดวิวที่ดึงมาเป็นค่าปัดเศษที่ TikTok แสดง (เช่น "69.4K") ไม่ใช่ตัวเลขที่แน่นอน
