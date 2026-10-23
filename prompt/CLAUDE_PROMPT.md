# Loyihamni Claude'ga taqdim etish uchun prompt

Quyidagi matnni Claude (claude.ai yoki Claude Code)ga to'liq nusxa ko'chirib yuboring:

---

# BM Avtomatizatsiya — Transport Operations Bot (loyiha tanishuvi)

## Men kim va nima kerak

Men avtobus parkini boshqaruvchi tashkilotman (Farg'ona). Mening loyiham —
`bm.dtransport.uz` (Brutto avtobus tizimi) bilan ishlaydigan Python
avtomatizatsiya to'plami. Loyiha kunlik ishlamoqda, lekin men uni yanada
mustahkamlamoqchi va yangi funksiyalar qo'shmoqchiman.

Iltimos, loyihani tushunib, quyida berilgan savollar/talablar bo'yicha yordam
ber. Kod tilini o'zgartirmasdan, mavjud uslubda ishla. Uzbek tilida javob ber.

## Loyiha haqida qisqacha

- **Maqsad:** BM tizimidan kunlik reyslarni yuklab olish, haydovchi jadvallarini
  PNG/Excel qilib shakllantirish, Telegram orqali yuborish, oylik yig'ma
  hisobot tayyorlash, saytga Excel jadval yozish.
- **Texnologiya:** Python 3.14, `requests`, `python-dotenv`, `openpyxl`,
  `matplotlib`, `playwright`, `pytest`, PostgreSQL/SQLite (`psycopg`).
- **Platforma:** Windows (OneDrive papkasi), Task Scheduler orqali avtomatik
  ishga tushiriladi, `-X utf8` bilan.
- **Til:** Kod va foydalanuvchi matnlari o'zbek tilida.

## Asosiy tushunchalar

- **Firma (profil):** `profiles.json` da saqlanadi. Har firmada:
  - `profileId` — BM'dagi profil ID
  - `routeVariantId` — yo'nalish varianti ID
  - `routeName`, `start1`, `start2` — yo'nalish nomi va boshlanishlari
  - ixtiyoriy `username`/`password` — firmaning o'z BM kredensiallari
  - ixtiyoriy `ownerChatIds` — firma egasi Telegram chat ID'lari
- **Muhit:** `.env` — `BM_ENV=test|prod`, `BM_USERNAME`, `BM_PASSWORD`,
  `TG_BOT_TOKEN`, `TG_CHAT_ID`.
- **Auth:** OneID orqali; `tokens.json` da tokenlar saqlanadi; har kompaniya
  uchun alohida token fayli mavjud (`company_tokens_path`).
- **BM API cheklovi:** username/password bilan to'g'ridan-to'g'ri API login
  OneID sabab **401 qaytaradi** — login faqat brauzer (Playwright) orqali
  amalga oshadi. Shuning uchun botda firma aniqlash saqlangan kredensiallarni
  solishtirish orqali ishlaydi.

## Kod tuzilishi (joriy)

```
bm_automation/
├── __main__.py, main.py      # CLI kirish nuqtasi
├── bot.py                    # Telegram bot (getUpdates long-poll)
├── daily.py, schedule.py     # kunlik vazifa va rejalashtirish
├── client.py                 # BM API HTTP-klienti (auth + refresh)
├── config.py, tokens.py      # env sozlamalar va token saqlash
├── profiles.py, state.py     # profillar va holat fayllari
├── notify.py                 # Telegram yuborish
├── browser_login.py          # OneID brauzer avtorizatsiyasi
├── duty.py, driver_sheet.py  # duty va haydovchi jadvali PNG
├── excel_fill.py, export_fill.py  # saytga Excel yozish
├── gross_reports.py, waybill_reports.py  # hisobotlar
├── reports.py, summary.py    # operativ va yig'ma hisobotlar
├── cli/                      # parser, commands, db_commands, common
├── scripts/
├── app/                      # QAYTA TASHKIL ETILGAN QATLAM (refactor)
│   ├── core/
│   │   ├── companies.py      # ko'p-firmali client + detect_company
│   │   ├── profiles.py, state.py, tokens.py
│   ├── api/client.py         # BMClient (yangi joyda)
│   ├── services/             # daily, duty, gross, waybill, report,
│   │                         # summary, export, driver_sheet,
│   │                         # excel_fill, stats
│   ├── db/                   # base, models, schema, storage, sync
│   ├── dashboard/            # metrics, server, web/index.html
│   ├── notifications/
│   │   ├── telegram.py       # telegram_call primitiv
│   │   └── ops/              # OPERATIONS BOT (asosiy bot)
│   │       ├── ops.py        # main, poll_forever, _handle_update
│   │       ├── dispatch.py   # handle_message, handle_callback (role-gated)
│   │       ├── reg.py        # interaktiv addcompany + login flow
│   │       ├── roles.py      # ADMIN/DISPATCHER/VIEWER/MANAGER
│   │       ├── render.py     # statistika render
│   │       ├── kb.py, context.py, legacy.py, text.py
│   │       ├── assistant.py  # AI-yordamchi (OpenRouter/lokal)
│   │       ├── daily_summary.py, problem_alerts.py  # avto-xabarlar
│   │       ├── self_review.py # AI o'z-o'zini rivojlantirish (/insights)
│   │       ├── planning.py, driver_entry.py          # reja + davomat kiritish
│   │       └── openrouter.py # LLM ulanishi
│   ├── core/bot_settings.py  # bot orqali sozlamalar (km narxi, til)
│   ├── utils/                # logger (aylanadigan), io, cleanup, km, tgformat
│   └── config/settings.py    # Config, get_config
├── tests/                    # test_dashboard, test_db, test_ops_bot
├── profiles.json             # firma profillari
├── tokens.json               # tokenlar (git'da NAZORAT QILINMAYDI)
├── state/                    # status.json, bot.pid, registration.json
├── reports/                  # chiqish fayllari
├── .env / .env.example
├── bot.bat, dashboard.bat, daily.bat, run_daily.cmd
├── servislar.ps1 + servislar_{start,stop,restart,status}.bat
└── run_hidden.vbs
```

## Botning joriy funksiyalari (app/notifications/ops)

- Buyruqlar: `/start /today /month /routes /vehicles /drivers /trips /problems
  /reports /errors /status /export /sync /addcompany /login /resend /list
  /vehicle /driver /top /distance /schedule /attendance /ai /insights
  /settings /daily /alerts /plan /help`
- Role-based access: ADMIN (hammasi) / DISPATCHER (eksport) /
  MANAGER (faqat o'z firmasini ko'radi) / VIEWER (ko'rish).
- **Bot sozlamalari:** `/settings` orqali 1 km narxi va til (o'zbek/rus)
  bot'da o'zgartiriladi; `KM_RATE` env faqat fallback.
- **AI o'z-o'zini rivojlantirish:** `/insights` va CLI `insights` — loglar
  (DB errors + logs/bm.log) va kunlik takroriy muammolarni tahlil qilib
  tavsiyalar beradi; `AI_SELFREVIEW_HOUR` da kuniga bir marta ADMIN/DISPATCHER'ga
  yuboradi.
- **Aylanma tozalash:** bot tsikli kuniga bir marta `reports/` (30 kun) va
  `_exports_pending.json` (7 kun) dagi eski yozuvlarni tozalaydi; CLI: `cleanup`.
- **Ko'p-firmali rejim:** har firma egasi faqat o'z firmasini ko'radi; ADMIN
  barchasini. `ownerChatIds` orqali chat'ni firmaga biriktiramiz.
- **`/addcompany`** (ADMIN): interaktiv bosqichlar — firma nomi → routeVariantId
  → ega chat ID'lari → firma username → parol. Holat `state/registration.json`
  da per-chat saqlanadi (bot restart'da ham yo'qolmaydi).
- **`/login`** (firma egasi): interaktiv — username → parol. So'ng
  `companies.detect_company()` firmani aniqlaydi:
  1. Saqlangan kredensiallar bilan solishtirish (asosiy usul);
  2. Fallback: API login + `routeVariantId` orqali route'ga kirishni tekshirish
     (`gross/route` 200/500 bilan).
  Topilsa chat_id firmaning `ownerChatIds`iga qo'shiladi va statistika
  yuboriladi.

## Joriy holat va so'nggi o'zgarishlar

- Loyiha `git` repo (1 commit: `f4b8136`).
- `app/` qatlamiga refactor qilindi; bot endi kengaytirilgan: rollar tizimi
  (ADMIN/DISPATCHER/MANAGER/VIEWER), ko'p-firmali rejim, AI-yordamchi,
  AI o'z-o'zini rivojlantirish, bot sozlamalari, avto-xabarlar.
- DB PostgreSQL (Supabase) bilan ishlaydi; testlar SQLite dublikati orqali.
- Bot va dashboard Windows Task Scheduler orqali `bot.bat` / `dashboard.bat`
  bilan ishga tushadi; `servislar.ps1 -Action restart` ularni boshqaradi.
- Bot logi: `C:\Users\User\AppData\Local\Temp\opencode\bot.log` va
  `logs/bm.log` (aylanadigan).
- Botni ishga tushirish: `python -X utf8 -u -m bm_automation bot`
  (sinash: `--once`), dashboard: `python -m bm_automation dashboard --port 8080`.

## Men biladigan ma'lum muammolar

1. `tokens.json` va `profiles.json` `.gitignore`da emas — tokenlar git tarixiga
   tushishi mumkin. (Tuzatildi: `.gitignore`ga qo'shildi.)
2. Bot buyruqlari uchun chat autentifikatsiya to'liq emas (rol tizimi bor, lekin
   default `TG_DEFAULT_ROLE` o'rnatilmagan bo'lsa hamma ADMIN deb qabul qilinadi).
   (Tuzatildi: qat'iy allowlist `is_allowed` — `TG_ALLOWED_IDS` yoki
   admin+dispatcher+chat birlashmasi.)
3. Jarayonlararo token race condition (`daily.bat` + `bot.bat` bir vaqtda
   `tokens.json` yozsa). (Tuzatildi: `atomic_write` hamma yozuvlarda.)
4. `get_logger` import paytida `sys.stdout`'ni ushlab qoladi — `--log` bayrog'i
   bilan `log.info` faylga chiqmaydi (faqat `print`). (Tuzatildi: modul logi
   `logs/bm.log` faylga ham yozadi.)
5. Logging `print`-asosida, darajalar/rotation yo'q. (Tuzatildi: aylanadigan
   fayl handler + darajalar.)
6. `requirements.txt`da `playwright` va `matplotlib` yo'q edi (tekshirish kerak).
7. Botda retry kam, xatolar ba'zi joylarda yutiladi. (Tuzatildi: `BMClient`
   exponential backoff + retry, `RateLimitError`.)
8. `reports/` papkasi tozalanmaydi — disk o'sishi. (Tuzatildi: `cleanup` — 30 kun.)
9. `_exports_pending.json` eski yozuvlar tozalanmaydi. (Tuzatildi: `cleanup` — 7 kun.)

## Mening rejam (tavsiya etilgan tartib)

1. **Xavfsizlik:** `.gitignore`ga `tokens.json`, `profiles.json`, `state/`,
   `*.bat`, `run_daily.cmd` qo'shish; bot'ga barcha buyruqlar uchun chat auth.
   (Bajarildi.)
2. **Token xavfsizligi:** token fayl qulfi, atomic write. (Bajarildi.)
3. **Retry va logging:** `client`/`notify`'ga retry+backoff, yagona logging
   moduli, `print`→`log` o'tish. (Bajarildi.)
4. **Kod tozalash:** DRY (save_json, _safe_name, month_start bir joyda).
5. **Arxitektura:** `core/api/services/telegram/jobs` qatlamlarini yakunlash.
6. **Testlar:** pytest to'plami kengaytirildi (312 test yashil, 137 s). `tests/conftest.py`
   bot sozlamalari (`state/bot_settings.json`) testlarini izolyatsiya qiladi; FERGANATEX/B-80
   duty-reja mosligi (`sync_duties` replace-sync) va PostgreSQL `delete_missing` column-major
   parametrlari ham test bilan qoplangan.

## Sizdan nima kutyapman

Iltimos, loyiha haqida savollaringiz bo'lsa so'rang (noaniq joylarni aniqlab
olish uchun). Keyin quyidagilardan birini bajaring:

1. Kod holatini tekshirib, jiddiy xatolar/xavfsizlik muammolari ro'yxatini
   bering (ustuvorlik bilan).
2. Yuqoridagi rejam bo'yicha bosqichma-bosqich ishni taklif qiling va men
   tasdiqlaganimdan keyin kodni yozing.
3. Hozir eng muhim deb hisoblagan 2-3 ishni ko'rsating va ularni bajarish
   tartibini bering.

Eslatma: loyiha Windows + OneDrive'da joylashgan, kod nomlashda `snake_case`,
matnlarda o'zbekcha (lotin) yozuv, hech qanday izohsiz (commentsiz) kod uslubi
afzal.

---

## Claude'ga berish bo'yicha maslahat

- **claude.ai** (vazba) uchun: yuqoridagi matnni kopiya qilib yuboring, so'ng
  loyiha fayllarini (yoki butun papkani) biriktirish imkoni bo'lsa — biriktiring.
- **Claude Code** (terminalda) uchun: yuqoridagi promptni `CLAUDE.md` yoki
  birinchi xabar sifatida bering, loyiha papkasida ochib ishlang:
  ```
  npx claude
  ```
  keyin promptni kiriting — Claude Code kodni o'zi o'qiydi.
- Hozirgi ish sessiyasini davom ettirmoqchi bo'lsangiz, "loyiha holatini
  yuqoridagi prompt asosida tushuntirib, kodni ko'rib chiq" deb so'rang.
