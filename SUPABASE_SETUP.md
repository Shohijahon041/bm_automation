# Supabase integratsiyasi

Loyiha Supabase'ga server tomondan bevosita PostgreSQL ulanishi orqali
ulanadi. `SUPABASE_DB_DSN` faqat lokal `.env` faylida saqlanadi; uni frontend,
Telegram yoki Git'ga uzatmang.

## Sozlash

1. Supabase loyihasida **Connect** oynasini oching va **Session pooler**
   connection string'ini nusxalang. Windows yoki IPv4 tarmoqlarida shu rejim
   ishlaydi.
2. Lokal `.env` ga qo'shing:

   ```env
   BM_DB_DRIVER=postgres
   SUPABASE_DB_DSN=postgresql://postgres.PROJECT_REF:PASSWORD@aws-REGION.pooler.supabase.com:5432/postgres
   ```

   `sslmode=require` URL'da bo'lmasa, dastur uni avtomatik qo'shadi.
3. Sxemani bulut bazasida yarating:

   ```powershell
   python -m bm_automation db init
   python -m bm_automation db status
   ```

4. BM ma'lumotlarini Supabase'ga yuklang:

   ```powershell
   python -m bm_automation db sync --route YOUR_ROUTE_ID --date 2026-08-13
   ```

Dashboard va avtomatik vazifalar endi shu Supabase ma'lumotlar bazasidan
o'qiydi va unga yozadi. Lokal SQLite fayli o'zgarmaydi.

## Muhim eslatmalar

- Desktop/uzoq ishlaydigan dastur uchun **Session pooler** (`:5432`) ishlating.
  Transaction pooler (`:6543`) ham ishlaydi; dastur undagi prepared statement
  cheklovini avtomatik hisobga oladi.
- Supabase'ning Service Role yoki anon API kaliti bu integratsiya uchun kerak
  emas: loyiha faqat server tomonidagi Postgres DSN bilan ulanadi.
- Pasport va guvohnoma rasmlari hali lokal `data/driver_documents` katalogida
  saqlanadi. Ularni Supabase Storage'ga ko'chirish alohida, ixtiyoriy qadam.
- `db init` sxemani to'liq yaratadi (shu jumladan `errors`, `automation_runs`,
  `route_daily` — ular AI o'z-o'zini rivojlantirish tahlili va solishtirish
  hisobotlari uchun ishlatiladi).
