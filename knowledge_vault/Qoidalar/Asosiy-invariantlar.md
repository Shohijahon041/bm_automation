# Asosiy invariantlar

> Bu hujjat — tizimning buzilmas qoidalari. Har qanday hisobot/tekshiruvdan
> oldin shu invariantlarga zid natija chiqsa — hisoblash xatosi bor.

## 1. Rejadagi = Amalda + Qabul qilinmagan

Har bir yo'nalishda:

```
reja_reys = amalda_reys + qabul_qilinmagan_reys
```

- Bu tenglik bajarilmasa — hisoblashda xato.
- `plan_reys`, `fact_reys`, `qabul_qilinmagan` — alohida manbalar.
- Ilova: [[km-hisoblash-xatolari]].

## 2. QATNOV ≠ AVTOBUS

- `trips` jadvalidagi har bir qator = **1 ta QATNOV** (voiture).
- Avtobuslar soni = `total_vehicles` / `vehicle_count` (distinct).
- "124 qatnov" deb yoziladi, "124 avtobus" deb **YOZILMAYDI**.
- `total_trips` va `total_vehicles` — alohida sonlar.

## 3. km-narx ustuvorligi

- Yo'nalish bo'yicha hisobda **km narx** (`km_rate`) aniqlanganda u asosiy.
- Qatnovlar soni bilan km farq qilganda — km qiymatiga ishoniladi.
- Tarif (`KM_RATE`) route profili uchun ustuvor.

## 4. Da'vo = ma'lumot, hisob = kod

- Agent vergul/spot bilan **taxminiy raqam** yozmaydi.
- Arifmetika (foiz, yig'indi, o'rtacha) — tool/kod natijasi.
- LLM natijasi hisob manbasi emas — faqat format.
- Raqamning manbasi va sanasi ko'rsatiladi; manbasiz raqam — da'vo emas.

## 5. Bitta savol — bitta haqiqat manbasi

- DB va sayt ma'lumoti farq qilsa, **sanani** solishtirish kerak: biri eski,
  biri yangi bo'lishi mumkin.
- Hisobda avval DB (`route_plan`), keyin sayt `fact` tekshiriladi.
- Ikki manba bir xil nom bilan atalib, boshqa davrni ko'rsatsa — bu xato.

## 6. Tenant chegarasi hech qachon kengaytirilmaydi

- Non-admin foydalanuvchi **faqat o'z kompaniyasi** ma'lumotini ko'radi.
- Bloklangan/muddati o'tgan kompaniya → `403`.
- Xatoni "tuzatish" uchun scope'ni ochish — taqiqlanadi; xato manbasi
  aniqlanadi ([[Ruxsatlar-va-rollar]]).
- Sessiya o'qilmasa yoki kompaniya noma'lum bo'lsa — **fail-closed**.

## 7. Bilim bazasi — faqat egasi

- Vault ichida ichki qoidalar va xatolar bor.
- Dashboard: ADMIN **va** `super` bayrog'i.
- Bot: `TG_OWNER_IDS` yoki super akkauntga bog'langan `telegram_chat_id`.
- Boshqa ADMIN ham ko'rmaydi — rol yetarli emas, egalik kerak.

## 8. Vault chegarasi

- Har bir yo'l `Path.resolve()` + ota-papkalar zanjiri bilan tekshiriladi.
- Prefiks bo'yicha tekshirish **taqiqlanadi** (`/vault2` ham `/vault` bilan
  boshlanadi — [[path-traversal-vault]]).
- Faqat vault ichidagi `.md` fayl o'chiriladi; bitta qayd ≤ 400 KB.

## 9. Rejalashtirilgan yuborish — idempotent

- Timer/retry bir marta yuborilgan xabarni qayta yubormasligi kerak.
- Har bir avtomatik yuborish uchun holat fayli bo'ladi:
  `daily_monthly_sent.json`, `grafik_limit.json`, `group_departures.json`.
- Retry "xato bo'ldi" degani emas — "holat yozildi" degani
  ([[oylik-takror]]).

## 10. Maxfiy ma'lumot chegarasi

- `.env`, tokenlar, `profiles.json`, `tokens.json` — hech qachon chatga,
  logga, commitga chiqmaydi.
- Xatoni tuzatishda ham faqat **kalit nomi** aytiladi, qiymati emas
  ([[Xavfsizlik]]).

## Invariant buzilganda

1. Hisobni to'xtatish — noto'g'ri raqam yuborilmaydi.
2. Manbalarni sanasi bilan solishtirish.
3. Aniqlangan sababni [[Xato-jurnali]]ga yozish.
4. Tuzatishdan keyin test va jonli tekshiruv ([[Tekshirish-qoidalari]]).

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Bilim-bazasini-yuritish]]
- [[km-hisoblash-xatolari]]
- [[Tekshirish-qoidalari]]
- [[Xato-jurnali]]

<!-- avto-bog'liq:end -->
