# BM Avtomatizatsiya — Texnik Audit Hisoboti (2026-09-12)

- **Loyiha:** `bm_automation` (bm.dtransport.uz avtomatlashtirish)
- **Audit sanasi:** 2026-09-12 (qayta audit; oldingisi 2026-08-10 — pastda arxivda)
- **Holat:** 380/380 test yashil, P0–P3 topilmalar tuzatildi

---

## 1. Executive Summary

2026-08-10 auditidagi kritik muammolarning aksariyati hal qilingan: yagona
HTTP-klient (retry/backoff/timeout/token-redaction), `logging` asosidagi
strukturli loglar, atomik token yozish va refresh-race himoyasi, rolga asoslangan
Telegram ruxsatlari, `.gitignore` endi token/profil/.env fayllarini qoplaydi,
Docker + PostgreSQL + Supabase zaxira oqimi ishga tushirilgan.

Bu auditda topilgan va **bugun tuzatilgan** muammolar:

| Daraja | Muammo | Holat |
|---|---|---|
| P0 | `requirements.txt` ishchi kopyadan o'chirilgan — VDS'da `docker compose build` buzar edi | ✅ Git HEAD'dan tiklandi |
| P0 | `bm_automation/atto_tokens.json` (jonli Atto sessiya tokeni) va `backup/` (142 MB PII'li DB dump) `.gitignore`da yo'q edi | ✅ Qo'shildi, `git check-ignore` bilan tekshirildi |
| P1 | 10 test yiqilgan (5 ta `test_ops_bot` eski deny-model, 5 ta `test_verify` sana-bog'liq) | ✅ Joriy modelga moslashtirildi; 380/380 yashil |
| P2 | `DASHBOARD_TOKEN` hujjatlarida yo'q edi; SPA tokeni ~30 chaqiruvning faqat 1 tasida yuborardi | ✅ `.env.example`, DEPLOY_VDS.md, server-ogohlantirish va SPA wiring tuzatildi |
| P2 | docker-compose izohi "Supabase ishlatilmaydi" deb yozgan, `.env.example` esa Supabase backup'ni sozlagan | ✅ Izohlar moslashtirildi |
| P3 | 12 ta ishlatilmaydigan `atto_*` skript (8 tida hardcode kredensial!) paket ildizida; `tools/debug*.py`; ildizda ortiqcha loglar | ✅ `tools/atto_dev/` va `logs/`ga ko'chirildi |

## 2. Ochiq (tuzatilmagan) tavsiyalar

1. ~~**Atto parolini almashtiring**~~ — ✅ 2026-09-12: skriptlar endi
   `.env` (`ATTO_LOGIN`/`ATTO_PASSWORD`) dan o'qiydi (`tools/atto_dev/atto_env.py`),
   hardcode kredensiallar olib tashlandi. **Parolni baribir almashtiring** —
   u haftalar davomida ochiq matnda yotgan.
2. **Bitta TG_BOT_TOKEN = bitta mashina.** Windows watchdog va VDS konteyneri
   bir token bilan birga ishlasa, Telegram 409 (getUpdates conflict) qaytadi
   (8-sentabr logida ko'rilgan). Faqat bittasida ishga tushiring.
3. ~~**Retention siyosati yo'q**~~ — ✅ 2026-09-12: `logs/` ham tozalanadigan
   bo'ldi (`cleanup.prune_logs`, 30 kun, `BM_LOGS_RETENTION_DAYS`; faol `bm.log`
   va log bo'lmagan fayllar tegilmaydi). `reports/` (30 kun) allaqachon edi.
   `data/` (haydovchi rasmlari/hujjatlari) **ataylab** TTL'dan tashqarida —
   bu arxiv ma'lumot, o'chirish kerak emas.
4. **Repo tarixi:** hali ham bitta commit + ~170 o'zgartirilgan fayl commit
   qilinmagan holatda. Birinchi commitdan oldin `git status`ni secretlar bo'yicha
   yana bir marta tekshiring (`git check-ignore` hamma maxfiy yo'llarni bloklaydi).
5. **Muhit farqi:** lokal Python 3.14 (Windows), konteyner 3.12 — asosiy oqimlar
   uchun muammo emas, lekin CI qo'shsangiz versiyani bir xillashtiring.
6. ~~`psycopg_pool` DeprecationWarning~~ — ✅ 2026-09-12: `ConnectionPool(open=True)`
   aniq ko'rsatildi (`app/db/base.py`).

## 3. Tuzatishlar tafsiloti (2026-09-12)

- `requirements.txt` — git HEAD'dan tiklandi (requests, python-dotenv, openpyxl,
  matplotlib, playwright, pytest, psycopg[binary]).
- `.gitignore` — `atto_tokens.json` va `backup/` qo'shildi.
- `tests/test_verify.py` — fake site klienti endi haqiqiy API kabi faqat
  so'ralgan oraliq ichidagi kunlarni qaytaradi; `test_month_bounds` sana-bog'liqlikdan
  xalos qilindi.
- `tests/test_ops_bot.py` — ochiq rejim (default) va qat'iy rejim
  (`TG_ALLOWED_IDS`/`TG_STRICT_ACCESS`, barcha manbalar birlashadi, dublikatsiz)
   shartnomasi assert qilinadi.
- `bm_automation/app/dashboard/server.py` — `run()`da: tashqi manzil + token
  yo'q bo'lsa ogohlantirish logi.
- `bm_automation/app/dashboard/web/index.html` — barcha API chaqiruvlar
  `bmFetch` orqali Bearer token yuboradi; token localStorage'da (`bm-token`),
  `<img>` rasmlari uchun `bm_token` cookie ham o'rnatiladi; 401 bo'lsa token
  bir marta so'raladi. `/api/users/{id}/photo` `img src` orqali cookie bilan.
- `.env.example` + `DEPLOY_VDS.md` — `DASHBOARD_TOKEN` hujjatlashtirildi
  (generatsiya buyrug'i bilan), Supabase zaxira izohlari to'g'irlandi.
- `docker-compose.yml` — izoh drifti tuzatildi.
- `bm_automation/atto_*.py` (12 fayl) → `tools/atto_dev/`; `backend2.*`,
  `backend_job.*` → `logs/`. `schedule.log` qoldi (`run_daily.cmd` faol yozadi).

## 4. Tekshirish

```
python -X utf8 -m pytest tests bm_automation/tests   # 380 passed
git check-ignore -v bm_automation/atto_tokens.json backup/
```

---

# Tarixiy audit (2026-08-10) — arxiv

Quyidagi hisobot o'sha kungi holat bo'yicha; ko'p tavsiyalar amalga oshirilgan
(yuqoriga qarang).

# BM Avtomatizatsiya — Texnik Audit Hisoboti

- **Loyiha:** `bm_automation` (bm.dtransport.uz avtomatlashtirish)
- **Joyi:** `C:\Users\User\OneDrive\7496~1\DEFAUL~1`
- **Audit sanasi:** 2026-08-10
- **Hajmi:** 20 modul, ~4 100 qator kod
- **Audit turi:** faqat tahlil — hech qanday kod o'zgartirilmadi
- **Amaldagi ma'lumotlar:** `reports/` da 41 fayl (~16.8 MB), `state/status.json` va `state/bot.pid` mavjud, `tokens.json` loyiha ildizida, `reports/_exports_pending.json` da eski `exp:` yozuvlari saqlanmoqda.

---

## 1. Executive Summary

Loyiha `bm.dtransport.uz` (Brutto avtobus tizimi) bilan ishlaydigan yagona Python paketi sifatida qurilgan: OneID orqali login, duty/waybill/gross/operativ hisobotlarni yuklash, Excel/PNG shakllantirish, Telegram bot va kunlik avtomatik vazifa (Windows Task Scheduler orqali). Funksional jihatdan ishlaydi va kundalik foydalanilmoqda.

Asosiy muammolar uch guruhda jamlanadi:

1. **Xavfsizlik (yuqori):** `tokens.json` va `profiles.json` `.gitignore`da yo'q — aksident kommit qilinsa token/ma'lumotlar git tarixiga tushadi. Telegram botda buyruqlar uchun chat autentifikatsiyasi umuman yo'q (`_allowed_chat` faqat document xabarlari uchun qo'llaniladi), botdan yuklab olinadigan fayl nomi path-traversal xavfiga ega. `browser_login` `ignore_https_errors=True` ishlatadi.
2. **Ishonchlilik (yuqori):** ikki/yoki undan ortiq jarayon (`daily.bat` + `bot.bat` + `schedule`) parallel ishlaganda `tokens.json` faylini bir-birini ustiga yozib tokenlarni buzishi mumkin (race condition). API so'rovlarida deyarli retry yo'q, `print`-asosida logging, xatolar ko'p joyda "yutilib" ketadi.
3. **Xizmat ko'rsatish (o'rta):** takroriy kod (`save_json`, `_safe_name`, JSON yozish, Excel yasash, Telegram yuborish), `requirements.txt`da `playwright` va `matplotlib` yo'q (yangi muhitda o'rnatilsa ishlamaydi), ko'p joyda hardcode (profil nomi `FERGANATEX`, ustun indekslari, vaqtlar).

Kod tartibli yozilgan (modullar kichik, nomlash tushunarli, API endpointlari hujjatlashtirilgan), lekin jarayonlararo muvofiqlashtirish, xavfsizlik va xatolarni kuzatish bo'yicha jiddiy tuzatishlar talab qiladi. Batafsil tavsiyalar 10–13-bo'limlarda.

---

## 2. Current Architecture

### 2.1 Umumiy tuzilma

```
Default Project/
├── bm_automation/            # asosiy paket (20 modul)
│   ├── __main__.py           # CLI kirish nuqtasi (argparse subcommands)
│   ├── client.py             # BM API HTTP-klienti (auth + refresh + download)
│   ├── config.py             # env sozlamalar (dotenv), base_url tanlash
│   ├── tokens.py             # tokens.json o'qish/yozish/tekshirish
│   ├── state.py              # state/status.json (ish tarixi) + bot PID qulfi
│   ├── notify.py             # Telegram xabar/fayl/rasm yuborish
│   ├── bot.py                # Telegram bot (getUpdates long-poll, tugmalar)
│   ├── profiles.py           # profiles.json (kompaniya profillari)
│   ├── browser_login.py      # OneID brauzer avtorizatsiyasi (Playwright)
│   ├── duty.py               # kunlik duty (haydovchi/avtobus/grafik)
│   ├── driver_sheet.py       # haydovchilar jadvali PNG-rasmi
│   ├── excel_fill.py         # Excel orqali saytga jadval yozish
│   ├── export_fill.py        # "Yo'nalish Jadvali Export" ni to'ldirish
│   ├── gross_reports.py      # gross trip/route/finance hisobotlari
│   ├── waybill_reports.py    # yo'l varaqalari (waybill) hisobotlari
│   ├── reports.py            # operativ hisobotlar (REPORTS registry)
│   ├── summary.py            # oylik/yillik yig'ma Excel
│   ├── schedule.py           # rejalashtirilgan hisobot (sikl / --once)
│   └── daily.py              # kunlik vazifa (jadval rasm + oylik Excel)
├── .env / .env.example       # kredensiallar
├── tokens.json               # saqlangan tokenlar (Git-da NAZORAT QILINMAYDI!)
├── profiles.json             # profillar
├── state/                    # status.json, bot.pid
├── reports/                  # chiqish fayllari (gitignore qilingan)
├── *.bat, run_daily.cmd      # Task Scheduler ishga tushirish skriptlari
└── requirements.txt          # 3 ta bog'liqlik (yetarli emas!)
```

### 2.2 Ish oqimlari

- **CLI:** `python -m bm_automation <buyruq>` — `__main__.py` da ~16 ta subcommand: `login`, `login-browser`, `routes`, `gross-trip`, `gross-route`, `waybill`, `duty`, `drivers`, `summary`, `reports`, `list-reports`, `daily`, `schedule`, `bot`, `profiles`, `notify-test`.
- **Kunlik vazifa (`daily`):** har profil uchun ertangi kun jadvalini PNG qilib Telegram'ga yuboradi, oy boshidan bugungacha oylik yig'ma Excel'ni yuboradi; 10 marta/10 daqiqada qayta urinadi; har urinishda yangi OneID login.
- **Bot (`bot`):** uzluksiz `getUpdates` long-poll; `/status`, `/resend`, `/list`, `/export`; rasm/Excel'dagi "Qayta yuborish" va "Yuborish" tugmalari; .xlsx qabul qilib saytga jadval yozadi.
- **Scheduler (`schedule`):** soat HH:MM da hisobotlarni yuklab (ixtiyoriy Telegram), yoki `--once` bilan Task Scheduler uchun bir martalik.
- **Auth:** tokenlar `tokens.json`da saqlanadi; `client` har so'rovda Bearer qo'shadi, 401 bo'lsa refresh; buzilsa OneID headless login (`bot._ensure_client`, `daily._fresh_login`).

### 2.3 Texnologiyalar

`requests`, `python-dotenv`, `openpyxl`, `matplotlib`, `playwright`. Logging — standart `print`/`sys.stderr`. Python 3.14 (Windows, `-X utf8`).

---

## 3. Module Dependency Map

Qattiq (import) bog'liqliklar:

```
__main__.py ──> client, config, driver_sheet, duty, gross_reports, reports,
                 summary, waybill_reports   (+ lazy: bot, daily, notify, profiles,
                                             browser_login, schedule, export_fill)
bot.py ──────> config, notify, state         (+ lazy: client, daily, export_fill,
                                              excel_fill, profiles, browser_login)
daily.py ────> client, driver_sheet, notify, profiles, state, summary
               (+ lazy: browser_login)
driver_sheet ─> client, config, duty, waybill_reports   (+ lazy: notify, profiles)
excel_fill ──> client, config                (+ lazy: driver_sheet, duty)
export_fill ─> client, excel_fill, profiles
gross_reports ─> client, config
waybill_reports ─> client, config
duty.py ─────> client, config
summary.py ──> client, config, duty
reports.py ──> client, config
schedule.py ─> client, config, duty, gross_reports, waybill_reports
               (+ lazy: notify)
notify.py ───> config
client.py ───> config, tokens
tokens.py ───> (yolg'iz; json/os)
state.py ────> (yolg'iz; json/os/ctypes)
profiles.py ─> (yolg'iz; json/os)
browser_login ─> config, tokens               (+ lazy: profiles)
config.py ───> dotenv
```

**Kuzatuvlar:**
- Aylanish (cycle) yo'q — import grafi toza DAG.
- `notify` <-> `bot` bir-biriga xizmat qiladi; `export_fill` -> `excel_fill` -> `driver_sheet`/`duty` zanjiri uzun, lekin lazy import bilan boshqarilgan.
- Xizmatlar qatlami yo'q: `__main__.py` bevosita barcha modullarni chaqiradi; "service" abstraksiya o'rniga har modul o'z `run()` funksiyasiga ega.

---

## 4. Critical Problems

1. **Secret fayllar git nazoratidan himoyalanmagan.**
   - `tokens.json` (access/refresh token) va `profiles.json` `.gitignore`da yo'q (`.gitignore`da faqat `.env` bor). Repo hali hech qanday commit qilmagan — bu omad; lekin birinchi commitda tokenlar tarixga tushib qoladi. *(Security, Git)*
2. **Jarayonlararo token race condition.**
   - `daily.bat`, `bot.bat` (va `schedule`) mustaqil jarayonlar bo'lib, `save_tokens()` bir xil `tokens.json`ni o'qiydi-yozadi (o'qish-yozish atomik emas, lock yo'q). `bot._ensure_client` OneID login qilganda `daily` ishlayotgan bo'lsa, tokenlar bir-birini bekor qiladi. Log'larda "getUpdates 409 conflict" va qayta-ishga tushirishlar kuzatilgan — bu qisman natijasi. *(Concurrency, Token management)*
3. **`requirements.txt` to'liq emas.**
   - `playwright` va `matplotlib` ro'yxatda yo'q; `driver_sheet.py` va `browser_login.py` bevosita import qiladi. Yangi muhitda `pip install -r requirements.txt` bilan o'rnatilsa, dastur ishga tushmaydi. *(Dependency)*
4. **Botda chat autentifikatsiya yo'q.**
   - `_allowed_chat()` faqat `.xlsx` document xabarlarida tekshiriladi; `/status`, `/resend`, `/export`, `/list` komandalari hech qanday chat cheklovisiz ishlaydi. Bot tokeni oshkor bo'lsa, har qanday foydalanuvchi `/resend` bilan barcha profillarni qayta yuborishga majburlashi mumkin. Bundan tashqari `_allowed_chat` qaytardi: agar chat_id sozlanmagan bo'lsa → `not allowed` → `True` → hamma ruxsat oladi. *(Authentication, Telegram bot, Security)*

---

## 5. High Priority Problems

1. **Yuklab olinadigan fayl nomi path-traversal xavfi.**
   - `bot._process_document` → `_download_to_temp(..., Path("reports/plan"), filename)` — `filename` Telegram'dan keladi va hech qanday sanitizatsiyasiz ishlatiladi. `..\..\evil.xlsx` kabi nom loyiha ildizidan tashqariga yozilishi mumkin. *(Security, File management, Telegram bot)*
2. **Retry yetarli emas / API ishonchliligi.**
   - `client.request` 401 holatida refresh qiladi, lekin tarmoq xatolari (timeout, 5xx) uchun retry yo'q. `client.download` faqat bitta 401 takrorlashga ega. `notify.telegram_call`da ham retry yo'q. `daily` qayta urinish qiladi, lekin `schedule.run_daily_job` umuman retry qilmaydi. *(Retry, API requests, Error handling)*
3. **Silent error swallowing.**
   - `notify.send_report_summary` — `except Exception: pass`; `browser_login._capture_profiles` — `except: pass`; `driver_sheet.graph_start_direction` — xato bo'lsa `continue` va natijada `direction="UP"` (sukut) — noto'g'ri ma'lumot chiqishi mumkin; `excel_fill.resolve_driver_id/resolve_vehicle_id` API xatosida `""` qaytaradi — foydalanuvchi "haydovchi topilmadi" deb noto'g'ri xabar oladi. *(Error handling)*
4. **Yagona logging yo'q.**
   - Hammasi `print`/`sys.stderr`; fayllarga `> log` bilan yo'naltiriladi (`bot.bat`, `daily.bat`). Log darajalari, timestamps (stdout'da), aylanish (rotation) yo'q. Xatolarni keyin tahlil qilish qiyin. *(Logging)*
5. **`tokens.json` muddati va `has_valid_access_token` ishlatilmayapti.**
   - `tokens.has_valid_access_token()` `client.py`ga import qilingan, lekin hech qayerda chaqirilmaydi; `client.login()` eskirgan token bilan ham `load_tokens_from_file()` orqali qaytadi (tekshirishsiz) — natijada keraksiz 401+refresh sikli. `expires_at` 12 soatga hardcode qilingan. *(Token management, Dead code)*
6. **Fayl tozalash yo'q — disk o'sishi.**
   - `reports/` (41 fayl, ~16.8 MB) va `reports/plan/` (har document yuklashda) tozalanmaydi. Bot `_recent_files` har `/status` da `rglob` bilan butun daraxtni skanerlaydi — fayllar ko'paysa sekinlashadi. *(File management, Memory/resource, Performance)*

---

## 6. Medium Priority Problems

1. **Takroriy kod (DRY buzilishi):**
   - `save_json()` — `gross_reports.py:108` va `waybill_reports.py:99` (bir xil).
   - `_safe_name()` — `daily.py:34` va `driver_sheet.py:373` (bir xil).
   - JSON yozish bloki (`json.dump(... ensure_ascii=False, indent=2, default=str)`) — `duty.py`, `waybill_reports.py`, `gross_reports.py`, `reports.py` da takrorlangan.
   - `month_start` oy hisobi — `daily.py` `run_daily` va `run_daily_once` da dublikat.
   - `telegram_settings()` chaqiruv + token/chat tekshiruvi — `notify.send_message/send_document/send_photo` da 3 marta takrorlangan.
   - `DUTY` endpoint konstantasi `duty.py`, `excel_fill.py` da, `export_fill` esa `excel_fill.DUTY`dan import qiladi — yagona manba yo'q.
2. **Dead code / ishlatilmayotgan kod:**
   - `client.login_via_oneid()` — ishlatilmayapti (OneID faqat brauzer orqali).
   - `client.list_all()` va `delete()` — ishlatilmayapti.
   - `tokens.has_valid_access_token()` — import qilingan, chaqirilmaydi.
   - `gross_reports.finance_report/finance_download` — CLI'ga ulanmagan (`gross-finance` buyrug'i yo'q).
   - `reports.export_week_range()` — hech qayerdan chaqirilmaydi.
   - `__main__.py:72` `failed = [r for r in []]` — o'lik o'zgaruvchi.
   - `__main__.cmd_report_types` — `cmd_list_reports` bilan bir xil; `report-types` subparseri yaratilmagan, `cmd_report_types` faqat qo'lda chaqiriladi.
   - `state.acquire_lock` da `except Exception: return True` — fail-open (qulf buzilsa hammaga ruxsat).
3. **Hardcode:**
   - `bot.py:193` `get_profile("FERGANATEX")` — profil nomi kodga mahkamlangan.
   - `bot.py:99` "har kuni 20:00" — Task Scheduler vaqtidan farq qilishi mumkin.
   - `export_fill.py:81-84` ustun indekslari (`r,2`, `r,6`) va `P{idx}` grafik nomlash — format o'zgarsa sindiriluvchan.
   - `excel_fill.py:323` `"secondStartDir": "UP"` — doimiy UP.
   - `driver_sheet.py` `DEFAULT_KONECHKA`, `"10-yo'nalish"` fallback.
   - `tokens.py:31` `expires_at = +12h` — token haqiqiy yashash vaqti noma'lum.
   - `daily._fresh_login` `time.sleep(15 * attempt)` — qat'iy.
4. **Error handling bo'shliqlari:**
   - `notify.telegram_call` — `resp.json()` muvaffaqiyatsiz bo'lsa (non-JSON javob) `ValueError` yutilmaydi; HTTP status tekshirilmaydi.
   - `client._refresh` — bitta urinish; refresh-da 401 bo'lsa `login()` chaqiriladi, lekin login ham ishlamasa xato aniq emas.
   - `state._load/save_state`, `reports/_exports_pending.json` — yozuvlar atomik emas (crash paytida fayl buzilishi mumkin).
   - `bot._handle_export_callback` — `rec.pop` dan keyin fayl yo'q bo'lsa "Export fayli topilmadi" deydi, lekin store'dan allaqachon o'chirilgan — ikkinchi bosishda "topilmadi yoki yuborilgan".
5. **Windows muvofiqligi:**
   - `duty.to_excel` `chr(64+i)` — 26 ustundan oshsa xato (hozir 12 ustun, xavfsiz, lekin kengayishda xavfli).
   - `state._pid_alive` Windows uchun ctypes bilan — ishlaydi, lekin fayl qulf emas, faqat PID belgisi (PID qayta ishlatilishi mumkin).
   - `profile.json`/`tokens.json` yo'llari `BM_PROFILES_FILE`/`BM_TOKENS_FILE` bilan o'zgartirilishi mumkin — yaxshi, lekin `state` va `notify` (chat_id) ham shunday parametrlashtirilmagan.
6. **Scheduler dublikatlari:**
   - `schedule.run_scheduler` cheksiz sikl qiladi; ammo Task Scheduler uchun `--once` tavsiya qilingan — ikkala yo'l ham mavjud bo'lib, chalkashlik. `run_daily_job` xato bo'lsa `except` da yutiladi, retry yo'q.
7. **`_exports_pending.json` eski yozuvlar:**
   - `exp43861dc792`, `expc9a6a08b9a` va boshqalar — ba'zi `path` relative bo'lib qolgan ("fayl topilmadi"). Store hech qachon tozalanmaydi (faqat `pop` bosilganda). *(State management)*

---

## 7. Low Priority Problems

1. **`reports.py` REPORTS registry** — katta dict, lekin xatolar manzillari frontend bundle'dan qo'lda yig'ilgan; manba hujjati yo'q.
2. **`bot._handle_update`** — har callback uchun avval `answerCallbackQuery("Ish boshlandi...")` yuboradi, keyin ish; resend tugmasi bosilganda `rs:ALL` limiti `callback_data` 64 belgi — profil nomi uzun bo'lsa kesilishi mumkin (hozir `name or 'ALL'`, xavfsiz).
3. **`driver_sheet`** rang sxemasi, shriftlar kodda qat'iy; dizayn o'zgarishlari uchun konfig emas.
4. **`notify.command_keyboard`** — statik tugmalar; bot komandalar qo'shilsa sinxronlash kerak.
5. **`profiles.py`** `DEFAULT_PROFILES` faqat FERGANATEX — yangi muhitda qolgan profillar yo'q.
6. **`__main__.py`** `--attempts`/`--delay` default qiymatlari `daily`'dagi va `daily.py main()`'dagi defaultlar bilan dublikat.
7. **`tokens.json`** `obtained_at`/`expires_at` ISO, lekin `has_valid_access_token` faqat `expires_at`ga qaraydi, `base_url` farqiga emas.
8. **`state/status.json`** faqat oxirgi 20 yozuv — "last_run" bilan `runs[0]` dublikat saqlanadi.

---

## 8. Security Risks

| Xavf | Daraja | Joyi | Izoh |
|---|---|---|---|
| `tokens.json`, `profiles.json` git'da | Yuqori | loyiha ildizi, `.gitignore` | Access/refresh token tarixga tushishi mumkin. Birinchi commitdan oldin `.gitignore`ga qo'shish shart. |
| Bot komandalari uchun auth yo'q | Yuqori | `bot.py:_handle_text` | `/resend`, `/export`, `/status` har kimga ochiq. |
| Document filename path-traversal | Yuqori | `bot.py:_process_document` | `file_name` tozalanmasdan `Path("reports/plan")/filename` |
| `ignore_https_errors=True` | O'rta | `browser_login.py:142` | TLS sertifikat tekshiruvi o'chirilgan. |
| Tokenlar ochiq matn | O'rta | `tokens.json` | Fayl ruxsatlari yoki shifrlash yo'q. |
| `TG_BOT_TOKEN`/`BM_PASSWORD` `.env`da | O'rta | `.env` | `.gitignore`da — yaxshi; lekin fayl ruxsatlari nazorat qilinmaydi. |
| `_allowed_chat` fail-open | O'rta | `bot.py:256` | Chat sozlanmasa hamma ruxsat oladi. |
| `answerCallbackQuery` bilan spouf | Past | `bot.py` | Callback `answerCallbackQuery` barchaga ochiq — xabar edit faqat callback orqali. |
| Playwright profillar oqishi | Past | `browser_login` | `_capture_profiles` ma'lumotlarini ekranga print qiladi (role, tin). |

---

## 9. Performance Risks

1. **API chaqiruvlar portlashi (`driver_sheet`):** `graph_start_direction` har grafik uchun 2 so'rov (UP/DOWN); `build_rows` yana `waybill_report` (konechka) — har profil × grafik × kun. Kunlik `daily` barcha profillar uchun bu siklini takrorlaydi.
2. **`summary.collect_days`** — oy uchun kunma-kun ketma-ket API so'rovi (30+ chaqiruv); `run_summary` har `daily` ishlashida barcha profil uchun takrorlanadi.
3. **`excel_fill.resolve_ids`** — har noyob haydovchi/avtobus uchun alohida API so'rov (bulk emas). Ko'p qatorli Excel'da 10+ so'rov.
4. **`bot._recent_files`** — `rglob` butun `reports/` daraxti; fayllar ko'paysa `/status` sekinlashadi.
5. **Yuklab olish (`client.download`)** — stream orqali (yaxshi), lekin `browser_login` `page.evaluate` localStorage'ni o'qiydi — sekin emas.
6. **Matplotlib `make_image`** — har `driver_sheet` chaqiruvda yangi figure; `plt.close` qilinadi (yaxshi), lekin bir jarayon ichida ko'p chaqiruv memory o'sishi mumkin (bot 6 marta resend qilsa).
7. **`reports.generate_daily_reports`** — ketma-ket; parallelizatsiya mumkin.
8. **`_load_pending_exports`/`_save_pending_exports`** — har callback'da to'liq fayl qayta yoziladi (kichik, kam ta'sir).

---

## 10. Refactoring Plan

Refaktoring bosqichma-bosqich, funksionallikni buzmasdan:

### Faza A — Xavfsizlik va poydevor (avval)
1. `.gitignore`ga qo'shish: `tokens.json`, `profiles.json`, `state/`, `*.bat`, `*.cmd` (agar shart bo'lmasa), `run_daily.cmd`.
2. `requirements.txt`ga `playwright`, `matplotlib` qo'shish; `pip freeze > requirements.lock` (optional).
3. `bot.py`ga barcha komandalar uchun chat whitelist tekshiruvi qo'shish (`_allowed_chat`ni `_handle_text`/`_handle_update` bo'ylab qo'llash).
4. `_download_to_temp`da filename sanitizatsiya (`Path(name).name`, alfanumerik filter).
5. `tokens.json` yozishga fayl qulfi (lock) yoki `state.acquire_lock` kabi jarayonlararo mofaqiyatni qo'llash — `daily`+`bot` bir vaqtda token yozmasligi uchun.
6. `browser_login`da `ignore_https_errors=False` (yoki `verify` parametri bilan).

### Faza B — Ishonchlilik
7. `client`'da tarmoq retry (`urllib3 Retry` yoki `requests.Session` adapter) va `timeout` konfiguratsiyasi.
8. `notify.telegram_call`ga retry + JSON tekshiruvi; `send_*` funksiyalariga ochiq fayl xatolarni aniq ko'tarish.
9. `schedule.run_daily_job`ga per-hisobot retry va xatolarni `state`'ga yozish.
10. Logging moduli (`logging`) — `bm_automation/logging_config.py`, darajalar, fayl handler, rotation.

### Faza C — Kod tozalash
11. `save_json`, `_safe_name`, `month_start`, JSON-yozish va `telegram_settings` tekshiruvini yagona util modulga (`io_utils.py`, `tg.py`) ko'chirish.
12. Dead code'ni olib tashlash yoki ishlatilishini aniqlash: `login_via_oneid`, `list_all`, `delete`, `has_valid_access_token`, `finance_*` (agar kerak bo'lsa CLI'ga ulash), `export_week_range`, `cmd_report_types`.
13. `cmd_daily` va `daily.main()` (va `cmd_schedule`/`schedule.main`, `cmd_bot`/`bot.main`) — bir xil argparse dublikatlari; `__main__`'dan tashqari `main()`larni olib tashlash yoki shared runner yasash.
14. `_exports_pending.json` store'ni `state/` ga ko'chirish + TTL tozalash (eski yozuvlarni 24-48 soatda o'chirish).

### Faza D — Arxitektura (11-bo'limga qarang)
15. Service qatlami va konfiguratsiya/state markazlashuvi.

---

## 11. Recommended Architecture

Hozirgi — "barchasi bitta paketda, CLI + bot + schedule" monolit. Tavsiya qilinadigan qatlamlar:

```
bm_automation/
├── __main__.py               # faqat CLI dispatcher
├── core/
│   ├── config.py             # pydantic-settings/dataclass, env validation
│   ├── tokens.py             # token storage + refresh (lock bilan)
│   ├── state.py              # status.json (atomic write), PID lock
│   └── logging_config.py     # yagona logging
├── api/
│   ├── client.py             # BMClient (retry, download, auth)
│   ├── auth.py               # login/login_by_profile/refresh/browser_login
│   └── endpoints.py          # barcha URL'lar (config)
├── services/
│   ├── duty_service.py
│   ├── reports_service.py    # REPORTS registry + generate_*
│   ├── gross_service.py
│   ├── waybill_service.py
│   ├── summary_service.py
│   ├── export_service.py
│   └── sheet_service.py      # driver_sheet (PNG) + excel_fill (duty yozish)
├── telegram/
│   ├── notify.py             # yuborish primitivlari (retry, size limit)
│   ├── bot.py                # handlerlar, chat auth
│   └── keyboards.py
├── jobs/
│   ├── daily_job.py          # kunlik vazifa (trigger meta bilan)
│   └── scheduler.py          # `--once` / Task Scheduler
└── profiles/ (yoki core/)    # profiles.json
```

Prinsiplar:
- **Har bir xizmat bitta sorov/yaratish/yuborish siklini egallaydi** — `run()` o'rniga aniq nomlangan funksiyalar.
- **Konfiguratsiya markaziy** — `config.py` barcha env'ni bir joyda; hech qayerda `PROD_BASE_URL`/`FERGANATEX` hardcode yo'q.
- **File I/O markaziy** — barcha fayl yozish/oxqish atomic (temp + `os.replace`), encodings bir xil.
- **Retry/backoff markaziy** — `api.client` va `telegram.notify`da.
- **Jarayonlararo lock** — token yozishda `msvcrt.locking`/`portalocker`; bot va daily bir-biriga xalaqit bermasligi uchun.

---

## 12. Future Scalability

1. **Ko'p profillar / ko'p kompaniyalar:** hozir `profiles.json` + `profileId` (get-token-by-profile) — yaxshi poydevor. Kengaytirish: har profil uchun alohida token cache, parallel ishlash (`concurrent.futures`) har profil alohida.
2. **Ko'p hisobot turlari:** `REPORTS` registry yaxshi naqsh — yangi hisobot qo'shish 3-4 qator. JSON schema va type validation qo'shish.
3. **Ko'p muhit (test/prod):** `BM_ENV` orqali — yaxshi; muhitlararo kalit (key) boshqaruvi (Vault / env per-profile).
4. **Scheduler masshtabi:** `schedule` dan Task Scheduler'ga o'tish — `--once` allaqachon bor; future: APScheduler yoki systemd.
5. **Bot o'sishi:** getUpdates → webhook (uy server yoki proxy) yoki `python-telegram-bot`/`aiogram` kutubxonasi; hozirgi raw `requests` yondashuvi qo'lda callback boshqaruvini talab qiladi.
6. **Monitoring:** `state/status.json` allaqachon "last_run" — future: Prometheus/simple heartbeat, log aggregation.
7. **Fayl arxivi:** `reports/` ni sana/profil bo'yicha papkalash + TTL tozalash; cloud backup (OneDrive allaqachon).
8. **Testlar:** hozir test yo'q. Boshlash: `pytest` + `responses`/`vcrpy` API mock'lar, `tmp_path` file testlari.

---

## 13. Implementation Roadmap

Tavsiya etilgan tartib (har bosqich tekshirilishi mumkin bo'lgan natija bilan):

| # | Bosqich | Tuzatishlar | Taxminiy hajm |
|---|---|---|---|
| 1 | **Xavfsizlik shot-подготовка** | `.gitignore` (tokens/profiles/state/bat), `requirements.txt` to'ldirish, chat auth, filename sanitizatsiya | 0.5–1 kun |
| 2 | **Token xavfsizligi** | token fayl qulfi, atomic write, `has_valid_access_token` qo'llash, `expires_at` uchun API'dan real expiry | 0.5 kun |
| 3 | **Retry va logging** | `client`/`notify` retry+backoff, `logging_config` moduli, `print`→log o'tish | 1–2 kun |
| 4 | **Dead code/takror tozalash** | `io_utils`, `tg` util; `save_json`/`_safe_name`/`month_start` birlashtirish; o'lik funksiyalarni o'chirish/ulash | 1 kun |
| 5 | **Scheduler va bot mustahkamlash** | `schedule.run_daily_job` retry, `_exports_pending` TTL, `--once` docs, bot duplikat nusxa himoyasi test | 1 kun |
| 6 | **Arxitektura qatlami** | `core/api/services/telegram/jobs` — bosqichma-bosqich ko'chirish (regression xavfsiz) | 3–5 kun |
| 7 | **Testlar** | `pytest` boshlang'ich to'plami: config, tokens, reports registry, excel_fill parse | 2–3 kun |
| 8 | **Monitoring/future** | webhook yoki kutubxona bot, fayl arxiv + TTL, parallel profillar | reja asosida |

**Eng avval qilish kerak:** 1-bosqich (git xavfsizligi + bot auth) va 2-bosqich (token qulfi) — aks holda joriy ish rejimida tokenlarning buzilishi va botning noto'g'ri ishlatilishi xavfi davom etadi.

---

## Ilova A — 28 audit yo'nalishi bo'yicha xulosa

| # | Yo'nalish | Holat | Asosiy topilma |
|---|---|---|---|
| 1 | Arxitektura | Qoniqarli | Yagona paket, toza DAG import; qatlamlar yo'q (11-bo'lim) |
| 2 | Dependency | Muammoli | `requirements.txt` yetarli emas (playwright, matplotlib) |
| 3 | Takrorlangan kod | Yuqori | `save_json`, `_safe_name`, JSON/Excel/Telegram yozish dublikatlari |
| 4 | Dead code | O'rta | `login_via_oneid`, `list_all`, `delete`, `has_valid_access_token`, `finance_*`, `export_week_range`, `cmd_report_types` |
| 5 | Ishlatilmayotgan funksiyalar | O'rta | `reports.export_week_range`, `gross.finance_*` CLI'siz |
| 6 | Xatolik boshqaruvi | Muammoli | Silent swallow, aniq xatolar yo'q, yutilgan 401 |
| 7 | API requestlar | Qoniqarli | Retry yetarli emas; `download` 401 bir martalik |
| 8 | Authentication | Qoniqarli | Login/profile/refresh ishlaydi; `has_valid_access_token` ishlatilmayapti |
| 9 | Token management | Muammoli | Fayl qulfi yo'q, race; `expires_at` hardcode |
| 10 | Telegram bot | Muammoli | Chat auth yo'q, filename path-traversal |
| 11 | Excel generation | Qoniqarli | openpyxl; `chr(64+i)` cheklovi, style takror |
| 12 | File management | Muammoli | Tozalash yo'q, disk o'sishi, relative path muammolari |
| 13 | State management | Qoniqarli | `status.json`, PID lock bor; atomic write yo'q, fail-open |
| 14 | Scheduler | Muammoli | `schedule`+Task Scheduler chalkash; `run_daily_job` retry yo'q |
| 15 | Parallel execution | Muammoli | `daily`+`bot`+`schedule` token race |
| 16 | Retry | Muammoli | Faqat 401 refresh; tarmoq/5xx uchun yo'q |
| 17 | Logging | Muammoli | `print`-asosida, daraja/rotation yo'q |
| 18 | Security | Muammoli | tokens.json git'da, bot auth yo'q, HTTPS errors ignore |
| 19 | Performance | O'rta | driver_sheet API portlashi, ketma-ket hisobotlar, `rglob` |
| 20 | Memory/resource | O'rta | Matplotlib figuralar, fayl yig'ilishi, no-cleanup |
| 21 | Race condition | Muammoli | tokens.json o'qish-yozish lock'siz; state yozuvlari |
| 22 | Windows compatibility | Qoniqarli | ctypes PID check, `.bat`, `-X utf8`; `chr(64+i)` cheklovi |
| 23 | Task Scheduler | Qoniqarli | `--once` ishlaydi; log'lar ochiq fayllarga, xato nazorati zaif |
| 24 | BM API | Qoniqarli | Endpointlar hujjatlashtirilgan; retry/validation kam |
| 25 | OneID login | Qoniqarli | Playwright ishlaydi; `ignore_https_errors`, silent except |
| 26 | Profile management | Qoniqarli | profiles.json; `get_profile("FERGANATEX")` hardcode |
| 27 | Daily automation | Qoniqarli | Retry sikli bor; `daily.main()`/`cmd_daily` dublikat |
| 28 | Monthly reports | Qoniqarli | summary Excel ishlaydi; ketma-ket API chaqiruvlar |

---

## Ilova B — Kutilayotgan joriy holat eslatmalari

- **Bot:** hozir PID 29648 log faylda; getUpdates conflict xabarlari kuzatilgan (diagnostik getUpdates so'rovlari sababli).
- **`reports/_exports_pending.json`:** eski `exp:` kalitlari bor; ba'zilarining `path`lari relative — "fayl topilmadi" holati. TTL tozalash tavsiya etiladi.
- **Git:** repo `master` bo'limida hali hech qanday commit yo'q — xavfsizlik fixlarini (1-bosqich) birinchi commitdan OLDIN qilish muhim.

*Ushbu hisobot faqat tahlil natijasidir; kodga o'zgartirish kiritilmadi.*

---

## Ilova C — Yangilanishlar (2026-08-15)

Auditdan keyin bajarilgan ishlar:

| # | Audit xulosasi | Holat |
|---|----------------|-------|
| 1 | `tokens.json`/`profiles.json` `.gitignore`da emas | ✅ `.gitignore`ga qo'shildi; `tokens.json` atomic write orqali |
| 2 | Bot buyruqlari uchun chat auth yo'q | ✅ Qat'iy allowlist `is_allowed` (`TG_ALLOWED_IDS` yoki admin+dispatcher+chat); role: ADMIN/DISPATCHER/MANAGER/VIEWER |
| 3 | Token race condition | ✅ Barcha JSON yozuvlari `atomic_write` orqali; `threading.Lock` |
| 4 | `--log` faylga `log.info` chiqmasligi | ✅ `logger.py` aylanadigan fayl handler (`logs/bm.log`) + `tail()` |
| 5 | Logging `print`-asosida | ✅ Darajali, 1 MB x 5 aylanadigan fayl log |
| 7 | Retry yo'qligi | ✅ `BMClient`: exponential backoff + jitter, `BMRateLimitError` |
| 8 | `reports/` tozalanmasligi | ✅ `utils/cleanup.py` — 30 kunlik TTL; bot tsikli kuniga bir marta; CLI `cleanup` |
| 9 | `_exports_pending.json` eski yozuvlar | ✅ 7 kunlik TTL (`prune_pending_exports`, o'qishda ham) |

Yangi funksiyalar (auditdan keyin qo'shildi):
- **MANAGER roli** — `ownerChatIds` orqali firma egasi faqat o'z kompaniyasini ko'radi.
- **Bot sozlamalari** — `/settings`: 1 km narxi va til (o'zbek/rus) bot orqali.
- **AI-yordamchi** (`/ai`) va **AI o'z-o'zini rivojlantirish** (`/insights`, `self_review.py`) — loglar va takroriy muammolar tahlili, OpenRouter ixtiyoriy.
- **Avto-xabarlar** — `daily_summary` (ertalabki xulosa), `problem_alerts`, `self_review`.
- **Xatolar/muvaffaqiyatsiz avto-ishlar** — DB `errors` va `automation_runs` jadvallari.
- **Insights web-dashboard** — `server.py` `/api/insights` endpoint'i (self_review.analyze + lokal qoidalar tavsiyalari); `web/index.html` da "AI tahlil" sahifasi: xatolar, takroriy muammolar va tavsiyalar paneli.
- **MergedCell xatosi tuzatildi** — Excel kataklariga xavfsiz yozish yordamchisi `exporters/excel_cells.py` (`set_cell`/`resolve_cell`): birlashtirilgan katak ichiga yozish endi anchor katakka tushadi. `export_fill.py` va `daily_grafik.py` (kunlik grafik generatori) shu yordamchiga o'tkazildi.
- **Firma boshqaruvchisi @AlSafariy (486986)** — `TG_MANAGER_IDS=486986`, `profiles.json` da FERGANATEX (10-yo'nalish) ga `ownerChatIds` bilan biriktirildi; MANAGER faqat o'z firmasi ma'lumotlarini ko'radi, eksport/sync/tahrirlash huquqidan mahrum.
- **Bot qayta ishga tushirildi** — yangi kod (rollar, firma filtri, davomat, OY TARIXI foizi, settings, ZERO_MILEAGE) bilan ishlayapti; 486986 ga jonli tekshiruvda faqat FERGANATEX ma'lumotlari chiqayotgani tasdiqlandi.
- **Testlar** — SQLite dublikati bilan 312 test yashil (`tests/`). `tests/conftest.py` dagi autouse fixture bot sozlamalarini (`state/bot_settings.json`) testlardan izolyatsiya qiladi, shuning uchun bot orqali o'rnatilgan jonli km narxi test natijalarini buzmaydi.
