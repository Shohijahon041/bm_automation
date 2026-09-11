# VDS ga yuklash yo'riqnomasi (Docker + PostgreSQL)

Bu yo'riqnoma loyihani virtual serverda (VDS) Docker orqali ishga tushirish
uchun. Ma'lumotlar bazasi — PostgreSQL 17 (docker-compose'dagi `db` xizmati),
baza nomi: **bm_loyiha**. Supabase endi ishlatilmaydi.

---

## 1. Talablar

- VDS: Ubuntu 22.04/24.04 yoki Debian 12 (kamida 2 vCPU / 2 GB RAM / 20 GB disk)
- O'rnatilgan: Docker Engine + Docker Compose plugin
  ```bash
  docker --version
  docker compose version
  ```
  O'rnatilmagan bo'lsa (Ubuntu):
  ```bash
  curl -fsSL https://get.docker.com | sh
  ```
- Domen/IP va 8081 port ochiq (dashboard uchun)

## 2. Loyihani serverga yuklash

Ikkita usuldan biri:

**A) Git orqali:**
```bash
cd /opt
git clone <repo_url> bm_loyiha
cd bm_loyiha
```

**B) ZIP/FTP orqali:**
- Kompyuterda loyiha papkasini zip'lab, serverga yuklang va oching:
  ```bash
  unzip bm_loyiha.zip -d /opt/bm_loyiha
  cd /opt/bm_loyiha
  ```

## 3. .env faylini tayyorlash

`.env` **Git'ga qo'shilmagan** (maxfiy). Serverda kompyuterdagi `.env` dan
nusxa oling va quyidagilarni tekshiring:

```bash
cp .env.example .env
nano .env
```

Muhim maydonlar:
- `BM_ENV=prod` (yoki `test`)
- `BM_USERNAME` / `BM_PASSWORD` — dtransport.uz login ma'lumotlari
- `TG_BOT_TOKEN`, `TG_CHAT_ID`, `TG_ADMIN_IDS`, `TG_MANAGER_IDS`
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
- `AI_DAILY_SUMMARY`, `AI_SELFREVIEW`
- `DASHBOARD_TOKEN` — **albatta to'ldiring**: bo'sh bo'lsa dashboard API
  (haydovchi rasmlari va hujjatlari bilan) parolsiz ochiq bo'ladi. Token yasash:
  ```bash
  python -c "import secrets; print(secrets.token_hex(24))"
  ```
  Brauzerda birinchi kirishda sahifa token so'raydi (localStorage'ga saqlanadi).
  Cookie-based token ham qo'llab-quvvatlanadi (`bm_token`), server bu ikkala
  usulni ham qabul qiladi.

`BM_DB_DSN` ni **qo'lda o'zgartirmang** — Compose uni avtomatik
`postgresql://bm:<parol>@db:5432/bm_loyiha` qilib beradi
(`POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` o'zgaruvchilari orqali —
parolni faqat serverdagi `.env` faylida saqlang, hujjatlarga yozmang).

Maxfiy o'zgaruvchilarni (masalan parolni) o'zgartirmoqchi bo'lsangiz,
`docker-compose.yml` dagi `POSTGRES_PASSWORD` standart qiymatini ham
yangilang — Compose `.env` dagi `${POSTGRES_PASSWORD:-...}` qiymatini ham
o'qiydi (`.env` ga `POSTGRES_PASSWORD=...` qo'shsangiz kifoya).

## 4. Ishga tushirish

```bash
cd /opt/bm_loyiha
docker compose up -d --build
```

Birinchi ishga tushirishda:
1. `db` xizmati `postgres:17` image'ni yuklab, `bm_loyiha` bazasini yaratadi;
2. `bot` jarayoni birinchi marta `init_db` orqali 18 jadvalni yaratadi;
3. Telegram bot ishga tushadi.

Holatni tekshirish:
```bash
docker compose ps          # 3 xizmat ham "Up" / "healthy" bo'lishi kerak
docker compose logs -f bot
docker compose logs -f dashboard
```

Dashboard: `http://SERVER_IP:8081`

## 5. Eski ma'lumotlarni ko'chirish (agar kerak bo'lsa)

Agar serverda bo'sh bazaga ishlayapsiz, lekin kompyuterdagi mahalliy PG
(bm_loyiha) yoki eski Supabase ma'lumotlarini keltirish kerak bo'lsa:

**Kompyuterda zaxira olish (allaqachon qilingan, `backup/` papkada):**
```bash
pg_dump --data-only --schema=public --no-owner --no-privileges \
  "postgresql://bm:<PAROL_SERVERDAGI_ENV_FAYLDA>@localhost:5432/bm_loyiha" \
  -f backup/bm_loyiha_data.sql
```

**Serverda tiklash** (dump faylini serverga yuklang, masalan `/opt/bm_loyiha/backup/`):
```bash
docker compose cp backup/bm_loyiha_data.sql db:/tmp/dump.sql
docker compose exec db psql -U bm -d bm_loyiha -v ON_ERROR_STOP=1 -f /tmp/dump.sql
```

Eslatma: dump jadvallar tayyor bo'lgandan keyin tiklanadi. Sxema avtomatik
yaratilmagan bo'lsa, avval bitta marta botni ishga tushiring (u `init_db`
bajaradi), keyin tiklang.

## 6. Yangilash

```bash
cd /opt/bm_loyiha
git pull                 # yoki yangi ZIP yuklang
docker compose up -d --build
```

## 7. Zaxira (backup)

Kunlik zaxira (cron) + eski nusxalarni avtomatik o'chirish (rotatsiya):
```bash
crontab -e
# har kuni soat 03:00 — zaxira:
0 3 * * * cd /opt/bm_loyiha && docker compose exec -T db pg_dump -U bm -d bm_loyiha | gzip > /root/backups/bm_loyiha_$(date +\%F).sql.gz
# har kuni soat 03:30 — 14 kundan eski dump'larni o'chirish (disk to'lib qolmasligi uchun):
30 3 * * * find /root/backups -name "bm_loyiha_*.sql.gz" -mtime +14 -delete
```

Eslatma: rotatsiya yo'q bo'lsa, zaxiralar ~40 MB/kun yig'iladi va 20 GB diskda
~1 yilda to'ladi — shuning uchun ikkinchi cron qatori majburiy.

## 8. Xavfsizlik eslatmalari

- `.env` fayli Git'ga qo'shilmasin (allaqachon `.gitignore` da).
- Dashboard'ni internetga ochmaslik uchun portni faqat kerakli IP'ga
  cheklang (ufw):
  ```bash
  ufw allow from <SIZNING_IP> to any port 8081
  ```
- Supabase hozir **zaxira (backup)** uchun ishlatiladi: `.env` dagi
  `SUPABASE_BACKUP_DSN` ko'rsatilgan bo'lsa, app avtomatik unda nusxa oladi
  (`BACKUP_INTERVAL_HOURS` oralig'ida). Supabase proyektini pauza qilsangiz,
  backup ham ishlamaydi.

## 9. Muhim: fayllar qayerda saqlanadi

| Papka          | Ma'nosi                                             | Volume   |
|----------------|-----------------------------------------------------|----------|
| `state/`       | tokenlar, bot sozlamalari, profillar, status        | bind     |
| `reports/`     | yaratilgan hisobotlar (Excel/PNG)                   | bind     |
| `data/`        | qo'shimcha ma'lumotlar                              | bind     |
| `logs/`        | bm.log va bot stdout/stderr                         | bind     |
| `pgdata`       | PostgreSQL ma'lumotlari (named volume `pgdata`)     | volume   |

Bind papkalarini ochishdan oldin yarating:
```bash
mkdir -p state reports data logs
```

## 10. Foydali buyruqlar

```bash
docker compose logs -f --tail=100 bot
docker compose restart bot
docker compose down          # to'xtatish (baza saqlanadi)
docker compose down -v       # BAZA BILAN BIRGA O'CHIRISH (zaxira qiling!)
python -m bm_automation db status   # jadval qatorlari (server ichida emas)
```
