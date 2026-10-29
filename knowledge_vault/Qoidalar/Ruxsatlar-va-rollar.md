# Ruxsatlar va rollar

Ikki tomon bor: **dashboard** (panel) va **Telegram bot**. Ular bir xil
bo'lim kalitlaridan foydalanadi, lekin rol reytingi biroz farq qiladi.

## Dashboard rollari

| Rol | Rank | Ma'nosi |
|---|---:|---|
| VIEWER | 0 | Kuzatuvchi — faqat ko'rish |
| DISPATCHER | 1 | Dispetcher |
| MANAGER | 2 | Menejer |
| DIRECTOR | 3 | Rahbar |
| ADMIN | 4 | Admin — barcha korxonalar |

**Muhim:** panelda `DISPATCHER (1) < MANAGER (2)`, botda esa
`DISPATCHER (3) > MANAGER (2)`. Sabab — botda dispetcher ko'proq buyruqqa
ega. Rolni solishtirganda qaysi tizim haqida gap ketayotganini tekshirish
kerak ([[Bot-sozlamalari]]).

## Panel bo'limlari (`PAGE_ROLES`)

| Minimum | Bo'limlar |
|---|---|
| 1 (DISPATCHER) | overview, dispatch, routes, vehicles, appeals, alerts, performance, gps, video |
| 2 (MANAGER) | drivers, top-performance, electricity, rejects, brutto, tariffs, dusers |
| 3 (DIRECTOR) | audit, sms, documents, knowledge* |
| 4 (ADMIN) | insights, myai, admin, logs, disk, users, settings |

`* knowledge` — **qo'shimcha `super` sharti** ham bor (pastga qarang).

### Per-user bo'limlar

- Foydalanuvchiga `pages` ro'yxati berilishi mumkin. Ro'yxat — **qisqartiruvchi**,
  rol esa **yuqori chegara**: rol ruxsat bermagan bo'lim ro'yxatga qo'shilsa
  ham ochilmaydi.
- `/api/me` `pages` va `pages_all` qaytaradi; `pages_all=true` bo'lsa
  foydalanuvchi barcha ruxsatli bo'limlarni ko'radi.
- Serverda ham tekshiriladi: `_GET_ROLE_MIN` / `_POST_ROLE_MIN` +
  `PAGE_API_PREFIXES`. UI'da tugmani yashirish — ruxsat nazorati emas.

### Moliyaviy cheklov

- Rank < 2 (VIEWER, DISPATCHER) uchun `drivers`, `rejects`, `electricity`,
  `top-performance` yashirin.
- `/api/routes` va `/api/summary` javobidan `skm`, `km_rate`, `tariff_*`
  maydonlari **serverda** olib tashlanadi (`_strip_finance`).

## Super (tizim egasi)

- `super` = ADMIN **va** (`DASHBOARD_ADMIN_USER` akkaunti yoki
  `dashboard_users.data.super` bayrog'i).
- Faqat super ko'radigan bo'lim: **Bilim bazasi** (`knowledge`).
- Botda: `TG_OWNER_IDS` yoki super akkauntga bog'langan `telegram_chat_id`.
- Boshqa ADMIN ham bilim bazasini ko'rmaydi — bu ataylab shunday.

## Tenant chegarasi

- ADMIN — barcha kompaniyalar.
- Qolgan rollar — faqat o'z `company`si; `routes` shu kompaniya bilan
  cheklanadi.
- Kompaniya bloklangan/o'chirilgan/muddati o'tgan bo'lsa — har bir API
  `403` qaytaradi (fail-closed).
- Guruh/kompaniya boshqaruvi ADMIN'da; menejer faqat o'z doirasida.

## Bot qoidalari

- Shaxsiy chatda rol `chat_id` bo'yicha; **guruhda** `from.id` bo'yicha
  aniqlanadi.
- Guruhda bot faqat quyidagilarga javob beradi:
  slash-buyruq, `@bot` mention, botga reply.
- Guruhda javob faqat ADMIN/DISPATCHER/MANAGER uchun; haydovchi shaxsiy
  chatda ishlaydi.
- Guruh xabarlari `group_stats.json`ga yoziladi, `bot_users.json`ga emas.
- Bo'lim ruxsatlari (`sections.py`) panel va bot uchun **bir xil**; yangi
  bo'lim qo'shilsa ikkala tomonda ham yangilanadi.

## Tekshirish ro'yxati

1. Non-admin boshqa kompaniya API'siga murojaat qilsa — `403`.
2. VIEWER moliyaviy API'ga murojaat qilsa — `403` yoki maydon yo'q.
3. ADMIN (super bo'lmagan) bilim bazasiga murojaat qilsa — `403`.
4. Per-user `pages` rol chegarasidan kengaytira olmaydi.
5. Bloklangan kompaniya sessiyasi hech qanday API'da ishlamaydi.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Bot-sozlamalari]]
- [[Atamalar]]
- [[Arxitektura]]
- [[Xavfsizlik]]

<!-- avto-bog'liq:end -->
