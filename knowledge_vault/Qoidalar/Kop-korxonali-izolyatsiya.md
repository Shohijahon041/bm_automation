# Ko'p korxonali izolyatsiya (tenancy)

> Bitta o'rnatishda bir nechta korxona ishlaydi. Har bir so'rov faqat o'z
> korxonasi ma'lumotini ko'rishi shart — bu qoida dashboard, bot va
> agentlarga birdek tegishli.

## Ma'lumotlar bazasi

- Har bir biznes jadvalida `company_id` ustuni bor; `company_id` bilan
  bog'langan jadvallar ro'yxati `app/db/schema.py::TENANT_TABLES`.
- So'rovlar doim `company_id` bo'yicha filtrlanadi — filtrsiz
  `SELECT` yozish taqiqlanadi.
- Yangi jadval qo'shilsa, u `TENANT_TABLES` ga kirmasa ko'p korxonali
  rejimda ma'lumot aralashib ketadi.

## Dashboard ruxsatlari

- Rol matritsasi: **DISPATCHER < MANAGER < DIRECTOR < ADMIN**
  (`app/dashboard/deps.py`), `dashboard_users` jadvalida saqlanadi.
- `company` maydoni = profil nomi; non-admin faqat o'z korxonasini ko'radi
  (`routeVariantId` scope, `_force_route`).
- Frontendda `PAGE_ROLES` ba'zi sahifalarni yashiradi; **server ham**
  tekshiradi (`_GET_ROLE_MIN`, `_POST_ROLE_MIN`) — frontendni chetlab
  o'tish mumkin, shuning uchun tekshiruv serverda majburiy.
- Moliyaviy maydonlar (`skm`, `km_rate`, `tariff_*`) DISPATCHER'ga
  yuborilmaydi: `_strip_finance()` javobdan olib tashlaydi; panelda esa
  `fin-block` bloklari `applyAccessUI` da o'chiriladi.
- Bilim bazasi (`/api/knowledge*`) va `/api/vault` — **faqat tizim egasi**
  (super-admin) uchun; boshqa administrator ham ko'rmaydi.

## Profillar va korxonalar

- `app/core/profiles.py` — korxona profillari (`name`, `routeVariantId`,
  `profileId`) va ularning BM kredensiallari.
- Bitta profil ostida bir nechta korxona bo'lishi mumkin; kredensiallar
  korxona darajasida saqlanadi va o'zgartirilganda profilning barcha
  yo'nalishlariga tegishli bo'ladi.
- Korxona qo'shish oqimi (onboarding) panelda: profil maydonlari
  to'ldiriladi, BM hisobi (OneID) tekshiriladi, so'ng barcha bo'limlar
  (grafik, hisobot, SMS, GPS) o'sha korxona uchun ulanadi.

## Tipik xatolar

- Filtrsiz so'rov → boshqa korxona ma'lumoti ko'rinadi (jiddiy).
- Frontendda yashirilgan tugma serverda tekshirilmagan → API orqali
  ochiq qoladi.
- `company_id` yozilmagan yangi jadval → hisobotlar aralashadi.

Bog'liq: [[Qoidalar/Ruxsatlar-va-rollar]] · [[Qoidalar/Dashboard-auth-va-sessiya]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ruxsatlar-va-rollar]]
- [[Arxitektura]]
- [[and-eco-token]]
- [[Bot-sozlamalari]]

<!-- avto-bog'liq:end -->
