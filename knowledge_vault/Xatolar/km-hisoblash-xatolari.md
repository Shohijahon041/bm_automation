# km-hisoblash xatolari

> Loyiha tarixi: avgust-10 yo'nalish sessiyasida `route` (B-80) bo'yicha
> km/reyslarni hisoblashda real xatolar. Shu sababga qaytib qolmaslik uchun.

## Xato 1 — reyslar tengsizligi (reja != amalda + qabul qilinmagan)

- **Belgi**: `plan_reys ≠ fact_reys + qabul_qilinmagan`.
- **Ildiz sabab**: bitta yo'nalish bo'yicha DB'dan olingan `plan`
  va saytdan olingan `fact` individual manbalar edi — biri eski sana,
  ikkinchisi yangi.
- **Qoida**: [[Asosiy-invariantlar]] №1 — tengsizlik bo'lsa, oldin
  ishonchli manba (DB `route_plan`) tekshiriladi, so'ng sayt fac't.
- **Tasdiqlovchi test**: `test_settings_set_route_skm_flow`.

## Xato 2 — km-narx ustuvorligi buzilishi

- **Belgi**: qatnovlar bo'yicha jami km va sayt jami km farq qiladi.
- **Ildiz sabab**: qatnovlar = trips soni, km esa boshqa birlikda.
- **Qoida**: [[Asosiy-invariantlar]] №3 — km-narx ustuvor.

## Oldini olish ro'yxati

1. Rejadagi = Amalda + Qabul qilinmagan tengligini tekshiring.
2. QATNOV/AVTOBUS farqini esda tuting.
3. Raqamni chamalab "to'ldirmang" — yetarli bo'lmasa ayting.