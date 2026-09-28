# BRUTTO-SHARTNEMA MASTER TIZIMI (116-son qaror + Master Prompt)

Bu hujjat **BRUTTO-SHARTNEMA MASTER SYSTEM PROMPT** (5 modulli)ning
loyihadagi amaliy talqini va `bm_automation/app/brutto/` paketi bilan
taqqoslash jadvalini o'z ichiga oladi.

Kod: `bm_automation/app/brutto/` (contract, tariff, monitoring, payment,
fund, master) — barcha izohlar o'zbekcha (lotin), uslub snake_case.

---

## Modul 1 — HUQUQIY VA SHARTNOMAVIY BIRIKTIRISH (`contract.py`)

Master prompt talabi:
- shartnoma muddati avtobusning o'rtacha yoshiga qarab **7 yilgacha**;
- tashuvchida GPS-tracker, ATTO validatorlar, dispetcherlik monitoringi
  va videokuzatuv **majburiy**;
- to'lov (SKM) har **12 oyda bir marta CPI** bo'yicha indekslanadi;
- yoqilg'i, elektr energiyasi yoki soliqlar **10% dan ortiq** o'zgarsa,
  shartnoma narxi qayta ko'rib chiqiladi.

Amalga oshirish:
- `contract_months(bus_age)` — yosh bo'yicha muddatlar jadvali
  (`<1` → 84 oy, `1-3` → 72, `3-5` → 60, `5-7` → 48, `7-9` → 36,
  `9+` → 12 oy minimal);
- `mandatory_equipment()` / `equipment_complete(equipment)` — majburiy
  uskunalar tarkibi va tekshiruvi;
- `indexed_price(skm, cpi)` — CPI bo'yicha indekslangan narx;
- `needs_review(fuel, electricity, tax)` — >10% o'zgarish tekshiruvi;
- `price_effective_from(start)` — narx kuchga kirish sanasi.

## Modul 2 — 1 KM NARX KALKULYATSIYASI (S_KM) (`tariff.py`)

Master prompt talabi (13-14-ilova + 4a-ilova):
- haydovchilar shtat me'yori = kunlik ish vaqti / 8 soat, fraktsiya
  yaxlitlash: `<0.25 -> 0.0`, `0.25..0.74 -> 0.5`, `>0.74 -> 1.0`;
- haydovchi oylik ish haqi = **statistika oylik ish haqi × 1.2**;
- S_KM = tannarx bloklari + davr xarajatlari + kredit foizi
  + **10% sof foyda** + **12% QQS**.

Amalga oshirish:
- `driver_staff_norm(hours)` — shtat me'yori yaxlitlash qoidasi;
- `driver_wage(stats_monthly)` — statistika × 1.2;
- `cost_blocks(blocks)` — tannarx bloklari (ijtimoiy soliq avto-hisob);
- `skm_total(...)` / `skm_full(cost, period, credit)` — to'liq S_KM.

Konstantalar: `STAFF_HOURS=8`, `WAGE_COEFF=1.2`, `SOCIAL_TAX=0.12`,
`NET_PROFIT=0.10`, `VAT_RATE=0.12`.

## Modul 3 — OPERATSION DISPETCHERLIK VA MONITORING (`monitoring.py`)

Master prompt talabi:
- L_f (amalda masofa) va K_amal (qatnovlar) GPS orqali real-vaqtda;
- **YTH, tirbandlik, GPS texnik nosozlik** bajarilmagan qatnov
  **sanalmaydi**.

Amalga oshirish:
- `is_excused(reason)` — asosli sabab tekshiruvi;
- `effective_kstjb(kstjb, excused_count)` — asosli sabablarni chiqarish;
- `effective_km(plan, actual, reason)` — asosli sababda rejani hisoblash.

## Modul 4 — SIFAT MEZONLARI VA JARIMALAR (`payment.py`)

Master prompt talabi:
- sifat indeksi `S = (K_amal - K_stjb) / K_amal × 100%`;
- `Total_Rate = alpha + beta + gamma`;
- `S_neto = S_KM × L_f × (1 − Total_Rate)` (additiv).

Loyiha standarti (116-son 32-band, saqlanadi):
- `S = SKM × Lf × (1 − alpha) × (1 − beta) × (1 − gamma)` (multiplikativ).

Amalga oshirish — **ikkala usul** `mode` kaliti orqali:
- `net_payment(..., mode="multiplicative")` (standart / 32-band);
- `net_payment(..., mode="additive")` (master prompt S_neto);
- `coefficients(lr, lf, kamal, kstjb, kmaq)` — alpha/beta/gamma + sifat
  indeksi (dashboard `brutto_calculator` jadvallari qayta ishlatiladi:
  alpha `0.20..0`, beta `0.15..0`, gamma `0.05..0`).

Eslatma: beta shkalasi ikkala manbada mos keladi
(`>=95% → 0%`, `90-95 → 3%`, `85-90 → 6%`, `80-85 → 9%`,
`70-80 → 12%`, `<70 → 15%`).

## Modul 5 — MOLIYAVIY JAMG'ARMA VA SUBSIDIYALASH (`fund.py`)

Master prompt talabi:
- yo'lkira tushumlari maxsus jamg'armaga, **1 ish kunida**;
- har **5-sangacha 35%** yoqilg'i avansi;
- jamg'arma tushumi shartnoma to'lovidan kam bo'lsa, farq **mahalliy
  budjetdan subsidiyalanadi**.

Amalga oshirish:
- `advance_amount(payment)` — 35% avans;
- `advance_due_date(month)` — oyning 5-sanasi;
- `subsidy_amount(fund, payment)` / `fund_balance(...)` — subsidiya;
- `fund_transfer_business_day(day)` / `next_workday(day)` — 1 ish kuni.

## Agregatsiya (`master.py` → `master_report`)

`master_report(...)` barcha 5 modul natijasini bitta lug'atga jamlaydi
(`module1_contract`, `module2_tariff`, `module3_monitoring`,
`module4_payment`, `module5_fund`, `summary`). U dashboard
`/api/brutto/master` endpoint'i orqali chiqariladi.

Dashboard: `bm_automation/app/dashboard/server.py` (GET `/api/brutto/master`,
`?mode=additive|multiplicative`) + `web/index.html` Brutto sahifasidagi
"MASTER PROMPT" paneli.

## Taqqoslash: master prompt ↔ loyiha

| Master prompt moduli | Ilgari loyihada | Endi |
|---|---|---|
| 1. Huquqiy/shartnomaviy | yo'q | `app/brutto/contract.py` |
| 2. 1 km narx (S_KM) | qisman (`dashboard/hisob.py` bloklari, km-rate) | `app/brutto/tariff.py` + `hisob.py` o'ram funksiyalari |
| 3. Dispetcherlik/monitoring | yo'q | `app/brutto/monitoring.py` |
| 4. Sifat/jarima | bor (32-band multiplikativ) | multiplikativ saqlanadi + additiv `mode` |
| 5. Jamg'arma/subsidiya | yo'q | `app/brutto/fund.py` |

Testlar: `tests/test_brutto_master.py` (5 modul + `master_report`),
`tests/test_dashboard.py` (`/api/brutto/master`).