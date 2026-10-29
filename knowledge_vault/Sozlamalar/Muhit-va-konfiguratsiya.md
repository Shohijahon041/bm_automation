# Muhit va konfiguratsiya

> Bu qaydda faqat **o'zgaruvchi nomlari** bor. Qiymatlar `.env`da yoki
> serverda saqlanadi va hech qachon hujjatga/chatga yozilmaydi
> ([[Xavfsizlik]]).

## `.env` guruhlari

| Guruh | Kalitlar | Izoh |
|---|---|---|
| BM | `BM_ENV`, `BM_USERNAME`, `BM_PASSWORD`, `BM_ORGANIZATION`, `KM_RATE` | BM login va global km narx |
| Baza | `BM_DB_DRIVER`, `BM_DB_DSN`, `SUPABASE_BACKUP_DSN`, `BACKUP_INTERVAL_HOURS` | Prod: PostgreSQL; test: SQLite |
| Dashboard | `DASHBOARD_TOKEN`, `DASHBOARD_ADMIN_USER`, `DASHBOARD_ADMIN_PASS`, `DASHBOARD_PORT` | Token bo'sh bo'lmasin |
| Telegram | `TG_BOT_TOKEN`, `TG_CHAT_ID`, `TG_ADMIN_IDS`, `TG_DISPATCHER_IDS`, `TG_MANAGER_IDS`, `TG_DRIVER_IDS`, `TG_ALLOWED_IDS`, `TG_OWNER_IDS`, `TG_DEFAULT_ROLE`, `TG_DRIVER_CHAT_ID`, `TG_DRIVER_ROUTE_CHATS` | Rollar va egalar |
| Avtomatik agentlar | `AI_AUTO_AGENTS`, `AI_DAILY_SUMMARY`, `AI_DAILY_SUMMARY_HOUR`, `AI_SELFREVIEW`, `AI_SELFREVIEW_HOUR`, `AI_SELFREVIEW_DAYS`, `FINES_REPORT` | Default: `off` |
| Guruh eslatmasi | `GROUP_DEPARTURE_NOTIFY` | Global o'chirish; guruh o'zi ham boshqaradi |
| Grafik | `BM_GRAFIK_REFRESH_IMAGE` | Jadval rasmini yangilash |
| LLM | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_APP_TITLE`, `OPENROUTER_HTTP_REFERER` | Ixtiyoriy |
| SMS gateway | `ANDROID_SMS_GATEWAY_URL`, `ANDROID_SMS_GATEWAY_LOGIN`, `ANDROID_SMS_GATEWAY_PASSWORD` | Android SMS gateway |
| GPS umumiy | `GPS_TCP_HOST`, `GPS_TCP_PORT`, `GPS_API_HOST`, `GPS_API_PORT`, `GPS_AUTOSTART`, `GPS_ONLINE_WINDOW_S`, `GPS_RETENTION_DAYS` | Teltonika + UI |
| GPS ruxsat | `GPS_AUTO_REGISTER`, `GPS_IMEI_WHITELIST`, `GEOFENCE_TELEGRAM`, `GEOFENCE_ALERT_COOLDOWN_S` | Qurilma qabul qilish |
| JT808/JT1078 | `JT808_ENABLED`, `JT808_TCP_PORT`, `JT1078_TCP_PORT`, `JT1078_MEDIA_HOST` | 4G kamera |
| ZLMediaKit | `GPS_ZLMEDIAKIT_RTMP`, `GPS_ZLMEDIAKIT_HTTP` | Video oqimi |
| Bilim bazasi | `KNOWLEDGE_VAULT_DIR` | Bo'sh bo'lsa loyiha ildizidagi `knowledge_vault/` |

**Muhim:** `.env` faqat jarayon startida o'qiladi. O'zgartirgandan keyin
xizmatni restart qilish shart.

## `state/` fayllari

| Fayl | Nima saqlanadi |
|---|---|
| `bot_settings.json` | km narx, tarif, avtomatik sync, `departure_offset_min`, `bot_driver_salary`, audit |
| `bot_users.json` | bot foydalanuvchilari va rollari |
| `group_departures.json` | guruh bo'yicha chiqish eslatmasi holati |
| `daily_monthly_sent.json` | oylik yuborilganini bildiruvchi dedup |
| `grafik_limit.json` | korxona bo'yicha kunlik grafik limiti |
| `dashboard_sessions.json` | panel sessiyalari |
| `driver_entry.json` | haydovchi bog'lash jarayoni |
| `daily_summary.json` | kunlik xulosa holati |

`state/` — **maxfiy va jonli holat**; deployda almashtirilmaydi.

## Sozlamalar ustuvorligi

1. **Env** — global default (`.env`).
2. **`state/bot_settings.json`** — panel/bot orqali o'zgartirilgan qiymat.
3. **Yo'nalish darajasi** — `route_km`, `route_skm`, `route_tariff`
   global qiymatdan ustun.

Misol: `KM_RATE` global, lekin yo'nalish uchun alohida narx kiritilgan
bo'lsa — o'sha narx ishlatiladi.

## Baza rejimlari

| Rejim | Qachon | Izoh |
|---|---|---|
| PostgreSQL | production | `BM_DB_DRIVER=postgres`, `BM_DB_DSN` |
| SQLite | testlar / lokal | `BM_DB_DRIVER=sqlite`; sxema `schema.py`dan |

`BM_TEST_MODE=1` faqat testlar uchun; tashqi so'rovdan bloklanadi.

## Tekshirish

```bash
# sozlama o'qilmoqdami
curl -s http://127.0.0.1:8080/api/settings
# GPS holati
curl -s http://127.0.0.1:8080/api/gps/health
```

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ish-oqimlari]]
- [[Integratsiyalar]]
- [[SMS-va-bildirishnoma-qoidalari]]
- [[Arxitektura]]

<!-- avto-bog'liq:end -->
