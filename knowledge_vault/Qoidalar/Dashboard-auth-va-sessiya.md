# Dashboard auth va sessiya

> Panelga kirish, sessiya muddati va foydalanuvchi boshqaruvi qoidalari.
> Kod: `app/dashboard/deps.py`, `app/dashboard/routers/auth.py`,
> `app/dashboard/routers/admin.py`.

## Parol va foydalanuvchilar

- Parollar `dashboard_users` jadvalida **pbkdf2-sha256** xesh bilan
  saqlanadi (`deps.py::hash_password`) — ochiq matnda hech qachon.
- Bootstrap admin: `.env` dagi `DASHBOARD_ADMIN_USER` / `DASHBOARD_ADMIN_PASS`
  dan jadval **bo'sh bo'lsagina** birinchi ishga tushishda yaratiladi.
  Keyingi adminlar Settings → "Dashboard foydalanuvchilari" orqali
  qo'shiladi (parolni chatga/lokka yozmang).
- `/api/dashboard-users` yangilash/o'chirishda `id` bo'lmasa `username`
  bilan topiladi — frontend faqat `username` yuboradi.

## Sessiya

- Kirishda `bm_session` nomli **httpOnly** cookie beriladi; muddat
  **12 soat** (`deps.py::_SESSION_MAX_AGE_S`), sessiyalar
  `state/dashboard_sessions.json` da saqlanadi.
- Cookie `secure` bayrog'i HTTPS so'rovida qo'yiladi — productionda
  panel faqat HTTPS orqali ishlashi kerak.
- Sessiya tokeni hech qachon logga, chatga yoki hujjatga yozilmaydi.

## Himoya choralari

- `POST` endpointlarida rate-limit (endpoint boshiga ~1 sekund).
- Test rejimi `BM_TEST_MODE=1` — haqiqiy kirish testlari bu o'zgaruvchini
  o'chirib oladi (testlarda autentifikatsiya talab qilinmasin).
- Panel sahifalari va API ruxsatlari bir xil rol matritsasidan olinadi:
  [[Qoidalar/Kop-korxonali-izolyatsiya]].

## Tipik xatolar

- `DASHBOARD_ADMIN_*` o'zgartirilib, bot/panel qayta ishga tushirilmagan —
  `load_dotenv()` import paytida bir marta o'qiladi.
- Cookie muddati tugagach panel "bo'sh" ko'rinadi — avval qayta kirishni
  tekshiring, keyin kodni.
- Sessiya fayli (`state/dashboard_sessions.json`) o'chirilsa barcha
  foydalanuvchilar qayta kirishi kerak bo'ladi (xato emas).

Bog'liq: [[Qoidalar/Ruxsatlar-va-rollar]] · [[Qoidalar/Xavfsizlik]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Xavfsizlik]]
- [[Arxitektura]]
- [[Asosiy-invariantlar]]
- [[vds-502-import]]

<!-- avto-bog'liq:end -->
