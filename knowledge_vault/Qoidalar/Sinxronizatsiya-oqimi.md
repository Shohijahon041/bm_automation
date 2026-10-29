# Sinxronizatsiya oqimi va fon agentlari

> Bot processida fon vazifalari poll-tsikl ichida ishlaydi. Har biri o'z
> holat fayli bilan "oxirgi marta qachon" ni eslab qoladi va takror
> yubormaslikka harakat qiladi.

## Ma'lumot oqimi

1. **BM API (`bm.dtransport.uz`)** — kunlik reyslar, haydovchilar, avanslar,
   jarimalar (`app/api/client.py`, `app/api/atto_client.py`).
2. **DB (PostgreSQL)** — `app/db/sync.py` API javobini jadvallarga yozadi;
   `python -m bm_automation db sync --route <id> --date <YYYY-MM-DD>`.
3. **Hisobotlar** — kunlik/oylik Excel, PDF, haydovchi grafik rasmi
   (`app/exporters/`).
4. **Yuborish** — Telegram bot, SMS gateway.

API javob bermasa (401/400) agentlar **to'g'ridan-to'g'ri PostgreSQL** dan
o'qiydi (`MYAI_DB_PRIMARY=true`) — bu rejim productionda standart.

## Fon agentlari (poll-tsikl)

| Agent | Nima qiladi | Boshqaruv |
| --- | --- | --- |
| `problem_alerts` | Muammoli reys/yo'nalish xabari | `AI_AUTO_AGENTS` |
| `doc_expiry` | Hujjat muddati tugashi | `AI_AUTO_AGENTS` |
| `monthly_results` | Oylik natijalar | `AI_AUTO_AGENTS` |
| `fines_report` | Jarima hisoboti | `AI_AUTO_AGENTS` + `FINES_REPORT` |
| `daily_summary` | Ertalabki xulosa | `AI_AUTO_AGENTS` + `AI_DAILY_SUMMARY` |
| `self_review` | `/insights` AI o'z-o'zini tahlili | `AI_AUTO_AGENTS` + `AI_SELFREVIEW` |
| `grafik_sms` | Grafik bo'yicha SMS | `AI_AUTO_AGENTS` |
| `group_departures` | Guruhda chiqish/obed eslatmasi | guruhdagi `/chiqish_on|off` (har doim ishlaydi) |
| `sync_auto` | BM → DB avtomatik sinxronizatsiya | `bot_settings.auto_sync` (std: on) |
| `grafik_update` | Grafik e'lon qilingandan keyin o'zgarishni kuzatish | har 15 daqiqa, har doim |
| `knowledge_grow` | Bilim bazasini kengaytirish | `KNOWLEDGE_AUTO` (std: on), har 180 daqiqa |

Holat fayllari: `state/sync_auto.json`, `state/grafik_sent.json`,
`state/grafik_late_warn.json`, `state/daily_sheet_sent.json`,
`state/knowledge_grow.json`.

## Qoidalar

- Bir xil xabar takror yuborilmasin — har agent "yuborildi" belgisini
  holat faylida saqlaydi ([[Sozlamalar/SMS-va-bildirishnoma-qoidalari]]).
- Fon agenti xatosi butun tsiklni to'xtatmasligi kerak: har chaqiruv
  alohida `try/except` ichida.
- `state/` fayllari git'ga tushmaydi — ular nusxalanmaydi, kerak bo'lsa
  qayta yaratiladi.
- Bot kodini yoki `.env` ni o'zgartirgandan keyin **jarayonni qayta
  ishga tushirish shart** (`load_dotenv()` import paytida bir marta).

Bog'liq: [[Ish-oqimlari]] · [[Sozlamalar/Ishga-tushirish-va-Deploy]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[SMS-va-bildirishnoma-qoidalari]]
- [[Ish-oqimlari]]
- [[Bot-sozlamalari]]
- [[Arxitektura]]

<!-- avto-bog'liq:end -->
