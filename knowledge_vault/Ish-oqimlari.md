# Ish oqimlari

Bu qayd — "tizim kun davomida nima qiladi va nima uchun shunday". Aniq vaqtlar
production (VDS) taymerlaridan olingan.

## Kunlik tartib (VDS)

| Vaqt | Xizmat | Nima qiladi |
|---|---|---|
| 20:00 | `bm-daily.timer` → `bm-daily.service` | Ertangi kun haydovchi jadvallarini tayyorlab Telegramga yuboradi (`daily --trigger scheduled`) |
| 22:00 | `bm-schedule.timer` → `bm-schedule.service` | Kunlik hisobot + DB sinxronlash (`schedule ... --notify`) |
| 06:30 va 18:30 | `bm-token-refresh.timer` | BM token keep-alive (refresh rotatsiyasi) |
| doimiy | `bm-bot.service` | Telegram long-polling: buyruqlar va avtomatik agentlar |
| doimiy | `bm-dashboard.service` | Panel API + GPS threadlari |

> `bm-daily` **ertangi kunni** yuboradi (`--offset 1` standart). Agar bir kun
> run to'xtab qolsa, o'sha kun avtomatik qoplanmaydi — qo'lda catch-up kerak:
> `daily --only <profil> --offset 0 --no-fresh-login`.

## Kunlik jadval (daily) bosqichlari

1. Har bir profil uchun BM'dan jadval olinadi; kerak bo'lsa token yangilanadi.
2. Har bir haydovchi uchun jadval PNG (va kerak bo'lsa Excel) tayyorlanadi.
3. Chat manzillari `bot_targets(route_id, chat_id)` orqali aniqlanadi —
   guruh + haydovchi shaxsiy chati.
4. Yuborishdan oldin limit va kunlik dedup tekshiriladi.
5. Yuborilgan kunlik xulosa bilim bazasiga qo'shiladi:
   `Hisobotlar/Kunlik-hisobot-YYYY-MM.md`.

## Oylik natijalar (monthly)

- Oy bo'yicha har bir haydovchining natijasi hisoblanadi va yuboriladi.
- **Dedup:** `state/daily_monthly_sent.json` — retry urinishlari oylik Excel'ni
  qayta yubormaydi. Ilgari 6 marta takror yuborilgan ([[oylik-takror]]).
- Natijalar `Hisobotlar/`ga `save_report` orqali saqlanadi.
- Dashboardda oylik **doim** ko'rinadi; botda haydovchi o'z oyligini faqat
  `bot_driver_salary` yoqilganda ko'radi.

## Grafik yuborish va limit

- Botdan grafik so'rash korxona bo'yicha **kuniga 20 ta** bilan cheklangan
  (`state/grafik_limit.json`, 00:00 da yangilanadi).
- PNG fayl atomik yoziladi — qayta yuborishda "file must be non-empty"
  xatosi chiqmaydi.
- Limit tugasa — foydalanuvchiga sababli xabar qaytadi, jadval yuborilmaydi.

## Haydovchi identifikatsiyasi

- Telegram foydalanuvchisi haydovchi sifatida DB'dagi `driver_id`ga bog'lanadi.
- Bog'lash jarayoni `state/driver_entry.json`da vaqtincha saqlanadi.
- Haydovchi faqat o'z reysi/oyligi bilan cheklangan ma'lumotni ko'radi.
- Guruhda javob faqat ADMIN/DISPATCHER/MANAGER uchun; haydovchi shaxsiy
  chatda ishlaydi.

## Guruhdagi chiqish/obed eslatmalari

- Har bir guruh o'zi uchun `/chiqish_on` yoki `/chiqish_off` bilan yoqadi.
- Vaqt `departure_offset_min` (-180..+180 daqiqa) bilan suriladi.
- Bu agent **`AI_AUTO_AGENTS`'ga bog'liq emas** — guruh sozlamasi bo'yicha
  mustaqil ishlaydi.

## BM token zanjiri

- BM qoidasi: VPS 24 soatdan ortiq BM'ga so'rov yubormasa refresh token
  eskirib, `401/403` bo'ladi.
- Shuning uchun har 12 soatda `keepalive`: refresh rotatsiyasi +
  `account-authorities` tekshiruvi.
- Muvaffaqiyatsiz bo'lsa — adminlarga Telegram ogohlantirish.
- **Muhim:** OneID brauzer login faqat lokal kompyuterdan bajariladi; BM
  VDS IP'sini bloklaydi. Token serverga fayl orqali ko'chiriladi
  ([[Integratsiyalar]]).

## Avtomatik agentlar (bot)

`AI_AUTO_AGENTS=on` bo'lsagina jadval bo'yicha ishlaydi; default — `off`,
ya'ni bot faqat buyruqlarga javob beradi.

| Agent | Vazifa | Sozlama |
|---|---|---|
| `daily_summary` | Kunlik xulosa (standart 08:00) | `AI_DAILY_SUMMARY`, `AI_DAILY_SUMMARY_HOUR` |
| `self_review` | Log/xato tahlili (standart 09:00) | `AI_SELFREVIEW`, `AI_SELFREVIEW_HOUR` |
| `problem_alerts` | Muammoli reyslar | `AI_AUTO_AGENTS` |
| `doc_expiry` | Hujjat muddati | `AI_AUTO_AGENTS` |
| `monthly_results` | Oylik natijalar | `AI_AUTO_AGENTS` |
| `fines_report` | Jarimalar | `FINES_REPORT` |
| `grafik_sms` | Grafik/ SMS eslatmalari | `AI_AUTO_AGENTS` |
| `group_departures` | Chiqish/obed eslatmasi | alohida (guruh `/chiqish_on`) |

Batafsil: [[Bot-sozlamalari]], [[SMS-va-bildirishnoma-qoidalari]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[SMS-va-bildirishnoma-qoidalari]]
- [[Arxitektura]]
- [[Integratsiyalar]]
- [[Bot-sozlamalari]]

<!-- avto-bog'liq:end -->
