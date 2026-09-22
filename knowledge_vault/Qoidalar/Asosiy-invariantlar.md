# Asosiy invariantlar

> Bu hujjat — tizimning buzilmas qoidalari. Har qanday hisobot/tekshiruvdan
> oldin shu invariantlarga zid natija chiqsa — hisoblash xatosi bor.

## 1. Rejadagi = Amalda + Qabul qilinmagan

Har bir yo'nalishda:

```
reja_reys = amalda_reys + qabul_qilinmagan_reys
```

- Bu tenglik bajarilmasa — hisoblashda xato.
- `plan_reys`, `fact_reys`, `qabul_qilinmagan` — individual manba.
- Ilova: [[Xatolar/km-hisoblash-xatolari]]

## 2. QATNOV ≠ AVTOBUS

- `trips` jadvalidagi har bir qator = **1 ta QATNOV** (voiture).
- Avtobuslar soni = `total_vehicles` / `vehicle_count` (distinct).
- "124 qatnov" deb yoziladi, "124 avtobus" deb **YOZILMAYDI**.
- `total_trips` va `total_vehicles` — individual sonlar.

## 3. km-narx ustuvorligi

- Yo'nalish bo'yicha hisobda **km narx** (`km_rate`) aniqlanganda u asosiy
  hisoblanadi.
- Qatnovlar soni bilan km farq qilganida — km qiymatiga ishoniladi.
- Tarif `KM_RATE` sozlamasi `route` profili uchun ustuvor.

## 4. Da'vo = ma'lumot, hisob = kod

- Agent vergul/spot bilan **taxminiy raqam** yozmaydi.
- Arifmetika (foiz, yig'indi, o'rtacha) — tool/kod natijasi.
- LLM natijasi hisob manbasi emas — faqat format.