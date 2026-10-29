# Integratsiyalar

## 1. BM (dtransport)

| Nima | Qiymat |
|---|---|
| Sayt | `bm.dtransport.uz` |
| API | `bmapi.dtransport.uz` |
| Login | **OneID brauzer** (parol login ishlamaydi) |
| Token fayli | `/opt/bm/bm_automation/tokens.json` |
| Yangilash | `bm-token-refresh.timer` — 06:30 / 18:30 |

**Qoidalar:**

- VPS 24 soatdan ortiq BM'ga murojaat qilmasa, refresh token eskiradi.
- Shuning uchun har 12 soatda keep-alive: refresh rotatsiyasi +
  `account-authorities`.
- BM **VDS IP'sini bloklaydi**: OneID brauzer login faqat lokal kompyuterdan
  bajariladi, `tokens.json` serverga fayl bilan ko'chiriladi.
- Profil bo'yicha token olish (`get-token-by-profile`) ba'zi akkauntlarda
  `401` beradi — bu BM tomonidagi cheklov; kod asosiy tokenga qaytadi
  ([[and-eco-token]]).
- Token va `profiles.json` — maxfiy; chatga/logga chiqmaydi
  ([[Xavfsizlik]]).

## 2. Telegram bot

- Long-polling; **bitta jarayon** (VDS). Ikkinchi poller conflict beradi
  ([[telegram-conflict]]).
- Shaxsiy chat: rol `chat_id` bo'yicha; guruh: `from.id` bo'yicha.
- Guruhda faqat slash-buyruq, `@bot` mention yoki botga reply'ga javob.
- Haydovchi jadvali PNG/Excel, oylik natijalar, ogohlantirishlar shu kanal
  orqali ketadi.
- Buyruqlar va sozlamalar: [[Bot-sozlamalari]].

## 3. GPS — Teltonika

| Parametr | Qiymat |
|---|---|
| Protokol | Teltonika (FMB920 va h.k.) |
| Port | TCP `5027` |
| Namuna buyruq | `setgprps 10,<SERVER_IP>,5027,0,TCP,1,,,codec8e` |
| Saqlash | `GPS_RETENTION_DAYS` (standart 30 kun) |
| Online oynasi | `GPS_ONLINE_WINDOW_S` (standart 600 s) |

- Yangi IMEI avtomatik ro'yxatga olinadi (`GPS_AUTO_REGISTER=1`), keyin
  panelda avtobusga bog'lanadi.
- Zona (geofence) alertlari Telegramga yuborilishi mumkin
  (`GEOFENCE_TELEGRAM`).

## 4. GPS — JT808 / JT1078 (4G kamera)

| Qatlam | Port | Vazifa |
|---|---|---|
| JT808 signaling | TCP `6808` | ro'yxat, heartbeat, buyruq (`0x9101`) |
| JT1078 media | TCP `6107` | video paketlari |

- Qurilmada bitta "server port" maydoni bo'lsa ham ikkala oqim to'g'ri
  ajratiladi; baribir standart sozlama tavsiya etiladi: **6808 signaling,
  6107 media**.
- Video jonli va xotiradan (SD karta) playback rejimida ishlaydi.
- Media server: **ZLMediaKit** (`GPS_ZLMEDIAKIT_RTMP`, `GPS_ZLMEDIAKIT_HTTP`)
  HTTP-FLV/HLS beradi.
- GPS quyi tizimi **`bm-dashboard` jarayoni ichida** ishlaydi
  (`GPS_AUTOSTART=1`) — alohida GPS systemd unit yo'q. GPS kodini qo'llash
  uchun `bm-dashboard` restart yetarli.

## 5. nginx va tashqi kirish

| Yo'l | Yo'nalish |
|---|---|
| `/` | dashboard `127.0.0.1:8080` |
| `/gps/`, `/api/gps/`, `/api/video/`, `/hls/` | GPS/Video UI `127.0.0.1:8081` |
| TLS | Let's Encrypt, TLS 1.2+, HSTS |

Tashqi TCP portlar: `5027` (Teltonika), `6808` (JT808), `6107` (JT1078).

## 6. PostgreSQL

- Production: PostgreSQL 17, `127.0.0.1:5432`, baza `bm_loyiha`.
- Kunlik zaxira: `/etc/cron.d/bm-backup` → `/root/backups/*.sql.gz`.
- Testlar: SQLite replikasi (`tests/sqlite_backend.py`).
- `SUPABASE_BACKUP_DSN` berilgan bo'lsa — qo'shimcha nusxa (ixtiyoriy).

## 7. Android SMS gateway

- `ANDROID_SMS_GATEWAY_URL/LOGIN/PASSWORD` orqali SMS yuboriladi.
- Kunlik limit va `sms_log` tekshiruvi: [[SMS-va-bildirishnoma-qoidalari]].

## 8. LLM (ixtiyoriy)

- OpenRouter (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`).
- `myai` agentlari va `/insights` tahlili.
- LLM hisob qilmaydi — faqat kod/tool natijasini sharhlaydi
  ([[Asosiy-invariantlar]] №4).

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[GPS-kuzatuv]]
- [[Arxitektura]]
- [[Ish-oqimlari]]
- [[Xavfsizlik]]

<!-- avto-bog'liq:end -->
