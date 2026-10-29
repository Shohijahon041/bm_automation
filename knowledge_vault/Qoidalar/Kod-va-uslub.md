# Kod va uslub

## Til va uslub

- **Izohlar, docstring va foydalanuvchiga ko'rinadigan matn** — o'zbek
  lotin alifbosida.
- Nomlar `snake_case`; qisqa va aniq.
- Izoh faqat **nima uchun** shunday qilinganini tushuntiradi; kod nima
  qilishini nomlar aytadi.
- Minimal patch: vazifaga kerak bo'lmagan refactor qilinmaydi.
- Mavjud foydalanuvchi ishi va fayllari saqlanadi; o'chirishdan oldin
  zaxira yoki nomini o'zgartirish.

## Muhit (Windows)

- Repo yo'li bo'shliq va kirillcha belgilardan iborat — har doim qo'shtirnoq
  bilan yoki ish papkasi sifatida beriladi.
- `python`/`py` aliaslari ishlamaydi. To'liq yo'l:
  `C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe`
- Har chaqiruvda `-X utf8` — o'zbekcha matn uchun.
- Lokal Python 3.14, CI 3.12.

## Paket tuzilmasi

- Yangi kod — `bm_automation/app/` ichida.
- Eski yuqori darajadagi modullar (`bot.py`, `daily.py`, `client.py` ...) —
  faqat moslik uchun; ularni kengaytirish emas, `app/`ga ko'chirish.
- Dashboard: `app/dashboard/` (FastAPI routerlar + `web/index.html` SPA).
- Bot: `app/notifications/ops/` (dispatch, render, kb, roles, agentlar).
- DB: `app/db/schema.py` (jadvallar) + `app/db/storage.py`.
- Umumiy: `app/core/` (profiles, companies, settings, sessions, knowledge).

## Testlar

- Repo ildizidan ishga tushiriladi:
  `python -X utf8 -m pytest tests/ -q`
- `tests/conftest.py` muhitni izolyatsiya qiladi: `bot_settings`, bilim
  bazasi papkasi va h.k. Jonli `state/` testga tushmaydi.
- `tests/sqlite_backend.py` — PostgreSQL o'rniga SQLite replikasi; sxema
  `schema.py`dan avtomatik yaratiladi.
- Patch qilishda **ishlatuvchi modul** nomini patch qilish kerak
  (masalan `monkeypatch.setattr(daily_summary, "get_storage", ...)`).
- Vaqtga bog'liq agentlarda interval hisoblagichlarini (`_LAST_CHECK_AT`)
  test oldidan reset qilish kerak.

## JavaScript (dashboard)

- `web/index.html` — bitta fayl; inline `<script>` va `<style>`.
- `await` ishlatadigan har bir funksiya `async function` bo'lishi shart
  ([[async-syntax-bug]]).
- O'zgartirishdan keyin inline scriptni ajratib `node --check`.
- Mavjud element ID va funksiya nomlarini sababsiz o'zgartirmaslik —
  SPA bog'lanishlari buziladi.

## Maxfiylik

- `.env` commit qilinmaydi, logga chiqmaydi, chatda ko'rsatilmaydi.
- Token/parol faqat `os.getenv` orqali o'qiladi; qiymat hech qayerda
  chop etilmaydi.
- Xato xabarida `Authorization` header yoki URL tokeni chiqmasligi kerak
  ([[Xavfsizlik]]).
- Repo **public** — commitdan oldin sir skani o'tkaziladi.

## Git tartibi

- Commit faqat so'ralganda qilinadi.
- `git push` faqat aniq so'ralganda; `master`ga force-push — taqiqlanadi.
- Commit xabari repo uslubida: qisqa, o'zbek yoki ingliz.
- Staging'ga fayl nomi bilan qo'shish; `.env`, token, `state/` hech qachon
  qo'shilmaydi.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Test-va-tekshiruv]]
- [[Tekshirish-qoidalari]]
- [[Bilim-bazasini-yuritish]]
- [[Xavfsizlik]]

<!-- avto-bog'liq:end -->
