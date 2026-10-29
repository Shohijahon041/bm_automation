# profiles ruxsati va sxemasi

> `profiles` bilan bog'liq ikki xil hodisa: fayl egaligi (faqat bitta
> firma ko'rinishi) va VDS sxemasidagi unikal cheklov (`ON CONFLICT`).
> Ikkalasi ham "ma'lumot bor, lekin ko'rinmayapti" ko'rinishida namoyon
> bo'ladi — shuning uchun bir qaydda.

## Hodisa A — `profiles.json` egaligi

### Semptom

- Dashboard'da va botda faqat **bitta firma** ko'rinadi; qolganlari
  yo'q.
- Fayl mavjud, JSON to'g'ri, lekin ilova uni o'qiy olmaydi.

### Ildiz sabab

`profiles.json` yoki uning ota-papkasi `root`ga tegishli bo'lib qolgan.
`bm` foydalanuvchisi faylni o'qiy olmaydi — xato yutiladi va ro'yxat
bo'sh/ bitta element bilan qoladi.

### Yechim

```bash
chown bm:bm <papka>/profiles.json
chown bm:bm <papka>
```

`chown` **fayl va papka** uchun birga bajariladi — faqat faylni
o'zgartirish yetarli emas (papkaga kirish huquqi ham kerak).

### Qoida

1. Deploydan keyin egalik tekshirilsin: `ls -l`, kutilgan `bm:bm`.
2. `scp`/`sudo` bilan ko'chirilgan fayl `root` bo'lib qolishi mumkin.
3. Xato yutilmasin — "fayl o'qilmadi" logda ko'rinsin.

## Hodisa B — `profiles.name` unikal (VDS sxemasi)

### Semptom

- `demo-seed` serverda
  `ON CONFLICT (name, route_id) ...` xatosi bilan to'xtaydi.
- Sabab: VDS `profiles` jadvalida `(name, route_id)` kompozit unikal
  indeks **yo'q** — faqat `name` unikal.

### Ildiz sabab

Lokal `bm_automation/app/db/schema.py`:

- `UNIQUE_KEYS["profiles"] = ["name", "route_id"]`;
- migratsiyada `DROP INDEX IF EXISTS uq_profiles_name`.

VDS'dagi jadval bu migratsiyani **hech qachon ko'rmagan** — eski sxema
(`name UNIQUE`) amalda qolgan. Test SQLite replikasi sxemani lokal
`schema.py`dan yaratadi, shuning uchun farqni ushlamaydi.

Bitta firmaning bir nechta yo'nalishi uchun alohida `profiles` qatori
kerak — `name` unikal bo'lsa ikkinchi yo'nalish yozilmaydi.

### Yechim — 2026-10-26 (asosiy, qo'llanildi)

VDS bazasi lokal sxemaga moslashtirildi — fayl darajasidagi workaround
olib tashlandi:

```sql
CREATE UNIQUE INDEX uq_profiles_name_route_id ON public.profiles (name, route_id);
DROP INDEX uq_profiles_name;
```

- `db/schema.py` va `db/storage.py` lokal (kompozit kalit) versiyada
deploy qilindi — `upsert_profile` endi `ON CONFLICT (name, route_id)`
bilan ishlaydi.
- Tekshiruv: mavjud nom uchun ikkinchi `route_id` qatori yozildi
  (2 qator), keyin `ROLLBACK` — baza 4 qatorda qoldi.
- Natija: bitta korxonaga bir nechta yo'nalish qo'shish mumkin.

### Yechim (eski workaround — tarix)

- Seed `profiles`ga yozmaydi; demo yo'nalishlar `routes` jadvalida
  saqlanadi.
- Yo'nalishlar `company_routes`, `metrics._companies` va `/api/me`
  routes-jadval fallback'i orqali olinadi.
- Kod izohida sabab yozib qo'yilgan — keyingi deploy'da qayta urinmaslik
  uchun.

### Qoida

1. Test SQLite replikasi **haqiqiy VDS sxemasini kafolatlamaydi**.
   `ON CONFLICT` yoki unikal indeksga tayanadigan yozuvdan oldin
   serverda `\d profiles` (yoki `pg_indexes`) tekshirilsin.
2. Lokal `schema.py` va VDS sxemasi farq qilsa — avval migratsiya
   qo'llanadi, keyin seed.
3. Seed idempotent bo'lsin: mavjud qatorni yangilaydi, dublikat
   yaratmaydi.
4. `init_db` migratsiyalarni **faqat bo'sh bazada** bajaradi — mavjud
   bazaga yangi unikal indeks kerak bo'lsa, DDL qo'lda qo'llanadi.

Bog'liq: [[Arxitektura]], [[Ishga-tushirish-va-Deploy]], [[Xavfsizlik]].

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ishga-tushirish-va-Deploy]]
- [[Xato-jurnali]]
- [[Arxitektura]]
- [[vds-502-import]]

<!-- avto-bog'liq:end -->
