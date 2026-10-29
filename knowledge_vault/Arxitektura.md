# Arxitektura

Tizim — avtobus korxonalari uchun transport konveyeri: BM saytidan kunlik
reyslarni oladi, haydovchi jadvallarini tayyorlaydi, Telegram orqali yuboradi,
oylik natijalarni hisoblaydi va barchasini panelda ko'rsatadi.

## Umumiy sxema

```
BM API (bmapi.dtransport.uz)
        │  sync / daily / monthly
        ▼
┌──────────────────────────────┐
│  PostgreSQL 17 (bm_loyiha)   │◄──── dashboard API (o'qish/yozish)
└──────────────────────────────┘
        │
        ├──► Telegram bot ──► guruh / haydovchi / admin
        ├──► Excel · PNG · PDF eksport
        ├──► Bilim bazasi (knowledge_vault/*.md)
        └──► GPS qatlami (Teltonika · JT808 · JT1078 · ZLMediaKit)

Brauzer ──► nginx (80/443) ──► FastAPI dashboard (127.0.0.1:8080)
                          └──► GPS/Video UI (ichki 8081)
```

## Qismlar

| Qism | Joy | Vazifa |
|---|---|---|
| Bot | `app/notifications/ops/` | Telegram long-polling, buyruqlar, avtomatik agentlar |
| Dashboard | `app/dashboard/` | FastAPI API + `web/index.html` SPA |
| Ma'lumot qatlami | `app/db/` | `schema.py` jadvallar, `storage.py` PostgreSQL |
| Yadro | `app/core/` | profillar, kompaniyalar, sozlamalar, sessiya, bo'limlar, bilim bazasi |
| Eksport | `app/exporters/` | haydovchi jadvali PNG, Excel/PDF |
| GPS | `app/gps/` | Teltonika, JT808, JT1078, to'xtashlar, solishtirma, UI |
| Brutto | `app/brutto/` | 116-son qaror: shartnoma, tarif, monitoring, to'lov, jamg'arma |
| LLM | `app/myai/` | ixtiyoriy multi-agent tahlil va `vault` tool |

> Eski yuqori darajadagi modullar (`bot.py`, `daily.py`, `client.py` va h.k.)
> moslik uchun qolgan. Yangi kod **`app/`** qatlamida yoziladi.

## Ma'lumot oqimi

1. **Sinxronlash** — BM API'dan kompaniya, yo'nalish, reys va haydovchi
   ma'lumotlari PostgreSQL'ga yoziladi.
2. **Kunlik job** — ertangi kun jadvali olinadi, har bir haydovchiga PNG
   jadval tayyorlanadi va Telegram orqali yuboriladi. Xulosa bilim bazasiga
   ham yoziladi ([[Ish-oqimlari]]).
3. **Oylik job** — oy yakuni hisoblanadi, natijalar haydovchilarga yuboriladi
   va `Hisobotlar/`ga saqlanadi. Takror yuborishdan dedup himoya qiladi.
4. **Panel** — barcha sahifalar bitta SPA; API'lar foydalanuvchi roli va
   korxonasi bo'yicha cheklanadi ([[Ruxsatlar-va-rollar]]).
5. **GPS** — qurilmalar TCP orqali ulanadi, koordinatalar bazaga tushadi,
   xarita va hisobotlar shu ma'lumotdan foydalanadi ([[Integratsiyalar]]).
6. **Bilim** — tasdiqlangan qoida/xato/hisobot markdown faylga yoziladi;
   panel, bot va LLM bir xil papkani o'qiydi ([[Bilim-bazasi]]).

## Ko'p korxonalilik (multi-tenant)

- Har bir foydalanuvchi bitta **kompaniya**ga bog'lanadi; ADMIN hammasini
  ko'radi, qolganlar faqat o'z kompaniyasini.
- Korxona darajasidagi cheklov `routeVariantId` / `company_id` orqali
  qo'llanadi; bloklangan yoki muddati o'tgan korxona → `403`.
- Kompaniyalar guruhlarga (`company_groups`) birlashishi mumkin; guruh
  eksklyuziv — bir kompaniya bitta guruhda.
- Tenant tekshiruvi **fail-closed**: sessiya o'qilmasa yoki kompaniya
  noma'lum bo'lsa — ruxsat berilmaydi.

## Saqlash joylari

| Joy | Nima saqlanadi | Zaxira |
|---|---|---|
| PostgreSQL | biznes ma'lumotlari (reys, haydovchi, hisob) | kunlik `pg_dump` |
| `state/` | sozlamalar, sessiyalar, limitlar, token holati | server zaxirasi |
| `reports/` | yaratilgan Excel/PNG hisobotlar | shart emas |
| `knowledge_vault/` | qoidalar, xatolar, hisobotlar | git + server zaxirasi |
| `bm_automation/tokens.json` | BM tokenlari (maxfiy) | alohida, hech qachon chatga chiqmaydi |

## Muhim texnik chegaralar

- **Sxema migratsiyasi (2026-10-26):** `profiles` endi `(name, route_id)`
  kompozit unikal indeks bilan — bitta korxonaga bir nechta yo'nalish
  qo'shish mumkin. Ilgari `profiles.name` unikal edi va deployda
  `ON CONFLICT` xatosi berardi ([[profiles-ruxsat]]). `init_db`
  migratsiyalarni **faqat bo'sh bazada** bajaradi — mavjud bazaga yangi
  indeks kerak bo'lsa, DDL qo'lda qo'llanadi.
- **Dashboard restart ~90 s** cho'zilishi mumkin: GPS threadlari SIGTERM'ga
  darhol chiqmaydi, systemd keyin SIGKILL qiladi. Bu zararli emas, lekin
  kutish kerak.
- **Yagona poller:** Telegram bot faqat bitta joyda ishlashi shart — VDS'da.
  Lokal botni parallel ishga tushirish `getUpdates Conflict` beradi
  ([[telegram-conflict]]).
- **BM IP cheklovi:** BM sayti VDS IP'sini bloklaydi; OneID brauzer login
  faqat lokal kompyuterdan bajariladi, token serverga ko'chiriladi
  ([[Integratsiyalar]]).

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ish-oqimlari]]
- [[Integratsiyalar]]
- [[Xavfsizlik]]
- [[Bot-sozlamalari]]

<!-- avto-bog'liq:end -->
