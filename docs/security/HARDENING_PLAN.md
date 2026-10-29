# BM Automation — hardening rejasi

Sana: 2026-10-29
Asos: `docs/security/SECURITY_AUDIT.md`
Holat: **lokal kod tuzatishlari bajarildi; productionga hech narsa chiqarilmagan.**

> Qoida: har qanday production o'zgarishi (deploy, restart, firewall, DNS, DB) faqat
> egasining aniq ruxsatidan keyin bajariladi. Ushbu hujjat — reja, bajarilgan ish emas.

---

## 1. Umumiy holat

| Bosqich | Holat |
|---|---|
| Lokal audit va zaifliklar ro'yxati | Bajarildi |
| Lokal kod tuzatishlari (SEC-01…SEC-05) | Bajarildi |
| Regressiya testlari (`tests/test_security_hardening.py`) | Bajarildi — 20 passed |
| Tenant/dashboard regressiya | Bajarildi — 130 passed |
| To'liq test to'plami | Bajarildi — 1549 passed, 0 failed (exit 0) |
| Production deploy | **Bajarilmadi — ruxsat kutilmoqda** |
| Production server tekshiruvi | **Bajarilmadi — ruxsat kutilmoqda** |

---

## 2. Tuzatilgan (lokal) — deploy navbati

| ID | O'zgargan fayl | Nima qilindi | Deploy turi |
|---|---|---|---|
| SEC-01 | `bm_automation/app/dashboard/routers/data.py` | `/api/salary` bo'sh qamrovda 403 | `bm-dashboard` restart |
| SEC-02 | `bm_automation/app/dashboard/routers/admin.py` | Kompaniya amallarida super/own-company guard | `bm-dashboard` restart |
| SEC-03 | `bm_automation/app/dashboard/routers/admin.py` | `BM_BASE_URL`, `BM_USERNAME` super-only | `bm-dashboard` restart |
| SEC-04 | `bm_automation/app/dashboard/fastapi_app.py`, `routers/system.py`, `deps.py`, `web/index.html` | CSRF Origin guard, backup POST-only | `bm-dashboard` restart + HTML |
| SEC-05 | `bm_automation/app/gps/api.py` | Bo'sh tokenda loopback-only, `compare_digest` | `bm-gps` restart |
| SEC-06 | `bm_automation/app/gps/api.py` | CORS whitelist (o'z hosti + `GPS_CORS_ORIGINS` + localhost), begona Origin'ga `Allow-Credentials` yo'q, `Referrer-Policy: no-referrer` | `bm-gps` restart |
| SEC-08 | `bm_automation/app/dashboard/deps.py` | Sessiya fayli `0600`; `DASHBOARD_TOKEN` `hmac.compare_digest` bilan | `bm-dashboard` restart |
| SEC-11 | `bm_automation/app/dashboard/routers/system.py` | `/api/health` — MANAGER+ (rank 2) | `bm-dashboard` restart |
| SEC-12 | `bm_automation/app/dashboard/deps.py`, `routers/auth.py` | PBKDF2 600 000 + versiyalangan hash (`pbkdf2_sha256$iter$hex`), eski 120k hash'lar qabul qilinadi va login'da avtomatik rehash | `bm-dashboard` restart |
| SEC-14 | `bm_automation/app/dashboard/routers/admin.py`, `fastapi_app.py`, `deps.py` | Non-super ADMIN faqat o'z kompaniyasini ko'radi; Host-header guard (DNS rebinding) | `bm-dashboard` restart |
| SEC-09 | `requirements.txt`, `.github/workflows/test.yml`, `Dockerfile` | Barcha paketlar `==` (production freeze'dan), action'lar commit SHA bilan, base image manifest-digest bilan qulflandi | CI + image build |
| SEC-13 | `app/dashboard/alerts.py` (yangi), `deps.py`, `db/backup.py` | Login xatolari (10 daq. ichida ≥5) va backup PARTIAL/ERROR → ADMIN'larga Telegram, tur bo'yicha throttle (10/30 daq.), fon oqimida | `bm-dashboard` restart |
| SEC-10 | `bm_automation/app/dashboard/fastapi_app.py` | CSP: `base-uri`, `form-action`, `object-src`, `frame-ancestors` qo'shildi; `'unsafe-inline'` sababi kod izohida | `bm-dashboard` restart |
| MFA | `app/dashboard/totp.py` (yangi), `routers/auth.py`, `db/schema.py`, `db/storage.py`, `db/backup.py`, `web/index.html` | Ixtiyoriy TOTP (har bir foydalanuvchi o'zi yoqadi, ADMIN birinchi navbatda): `dashboard_users.totp_secret`, `/api/mfa/setup|enable|disable`, login'da kod bosqichi, Settings paneli, admin `mfa_reset` (telefon yo'qolganda) | `bm-dashboard` + `bm-bot` restart (migratsiya `init_db()`) |
| — | `tests/test_security_hardening.py` | 10 yangi regressiya testi (jami 30) | repo |
| — | `tests/test_mfa_and_alerts.py` | 16 yangi test (TOTP vektorlar, alertlar, MFA API, CSP) | repo |

Eslatma: `web/index.html` statik fayl — server restart talab qilmaydi, lekin
`system.py`/`fastapi_app.py` Python o'zgargani uchun `bm-dashboard` restart shart.
MFA migratsiyasi (`ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS
totp_secret`) `init_db()` orqali start paytida qo'llanadi.

### 2.1 SEC-07 — DB roli va RLS qarori (2026-10-29, tekshirilgan)

Production tekshiruvi (faqat o'qish):

| Tekshiruv | Natija |
|---|---|
| `pg_roles` (joriy rol `bm`) | `rolsuper=f`, `rolbypassrls=f`, `rolcreatedb=f`, `rolcreaterole=f` — login-only rol |
| `bm_loyiha` egaligi | Jadvallar egasi `bm` (`relforcerowsecurity=false` → egasi RLS'ni chetlab o'tadi) |
| `listen_addresses` / `pg_hba.conf` | `localhost`; faqat loopback (peer + scram-sha-256) |
| `pg_tables.rowsecurity` | 29 jadvalda `t`, `pg_policy` bo'sh (0 policy) |

**Qaror: policy-asosidagi RLS joriy arxitekturada qo'shilmaydi.** Sabablari:

1. `init_db()` startda DDL (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`,
   jadval yaratish) bajaradi — shuning uchun `bm` jadval egasi bo'lib qolishi
   shart, egasi esa policy'siz RLS'ni chetlab o'tadi (foyda nolga teng).
2. Policy ishlashi uchun har so'rovda tenant GUC (masalan
   `app.current_company`) o'rnatilishi kerak — bu `psycopg_pool` ulanish
   kontekstini har request'da boshqarishni talab qiladi (murakkab, regressiya
   xavfi yuqori). Hozirgi izolyatsiya **ilova qavatida** majburiy:
   `Scope`/`tenant_guard` + `force_route` + `_strip_finance` (SEC-01, SEC-14).
3. Egasi bo'lmagan alohida `bm_app` roli yaratish — kelajakdagi alohida ish
   (owner `bm` DDL uchun, runtime `bm_app` faqat DML; RLS + `SET LOCAL` GUC).

RLS'ni hozir yoqib qo'yish (policy'siz) Supabase'ga ko'chirishda himoya
beradi va lokal PG'da egasi tufayli xatti-harakatni o'zgartirmaydi — shu holat
saqlanadi. DB porti tashqariga ochilmagan, shuning uchun tashqi to'g'ridan-to'g'ri
ulanish yo'li yo'q.

### Deploydan oldingi shartlar (production)

1. `git status` va diff'ni ko'rib chiqish; **`.env`, `state/`, `profiles.json`,
   `tokens.json` diff'ga tushmasligi**.
2. Repo PUBLIC — secret-scan: `git grep -nE "(TOKEN|SECRET|PASSWORD|DSN)=[^ ]+"` faqat
   namuna qiymatlar chiqishiga ishonch hosil qilish.
3. Testlar lokalda yashil (6-bo'lim).
4. Backup: o'zgaradigan fayllarning VDS nusxasi timestampli katalogga olinadi
   (deploy paytida, ruxsat bilan).
5. Deploy oynasi: foydalanuvchilar kam vaqtda; restart davomida dashboard qisqa
   uziladi.

---

## 3. Ochiq kamchiliklar — prioritet reja

### P1 (keyingi 1–2 hafta, production ruxsati bilan)

| ID | Ish | Fayl | Tekshirish |
|---|---|---|---|
| — | `bm_app` runtime roli (faqat DML) + policy-asosidagi RLS (SEC-07 §2.1, 3-variant) | `db/`, systemd | staging'da tenant A/B testlari |
| — | `pip-compile --generate-hashes` + `--require-hashes` (SEC-09 keyingi qadam) | `requirements*.txt` | CI yashil |

### P2 (keyingi oy)

| ID | Ish |
|---|---|
| SEC-10b | CSP'ni nonce/hash asosiga o'tkazish (inline handler'larni bosqichma-bosqich ajratish) |
| — | Super ADMIN uchun MFA'ni majburiy qilish (hozir ixtiyoriy) |
| — | Alertlarni Telegram'dan tashqari e-mail/webhook kanaliga chiqarish |

### Bog'liqlik yangilash (avtomatik emas)

1. `pip index versions <paket>` / `pip list --outdated` bilan mavjud versiyalarni
   ro'yxatlash (o'rnatmasdan).
2. Har paket uchun tavsiya etilgan versiya + moslik izohi jadvalga kiritiladi
   (FastAPI/Starlette/Pydantic birgalikda; psycopg_pool 3.2 API).
3. Yangilash alohida branch + to'liq pytest + lokal dashboard smoke test bilan.
4. Bir martada bitta ekotizim (masalan faqat FastAPI guruhi); rollback — lockfile.

---

## 4. Production server — faqat o'qish tekshiruvi (ruxsat bilan)

Quyidagilar **hech narsani o'zgartirmaydi**; natijalar `SECURITY_AUDIT.md`ga
qo'shiladi:

```bash
# Ochiq portlar va jarayonlar
ss -tlnp
ss -tnp | head -50

# SSH siyosati (samarali konfiguratsiya)
sshd -T | grep -Ei 'permitrootlogin|passwordauthentication|pubkeyauthentication|allowusers|maxauthtries'

# Firewall / fail2ban holati
ufw status verbose        # yoki: nft list ruleset
fail2ban-client status
fail2ban-client status sshd

# OS yangilanishlari (o'rnatmasdan)
apt list --upgradable 2>/dev/null | head -30

# Fayl ruxsatlari (qiymatlarni chop etmasdan)
stat -c '%a %U:%G %n' /opt/bm/.env /opt/bm/state /opt/bm/state/dashboard_sessions.json
ls -ld /opt/bm /opt/bm/state

# Servis izolyatsiyasi
systemctl cat bm-dashboard bm-bot bm-gps | grep -E 'User=|Group=|ProtectSystem|NoNewPrivileges|PrivateTmp'

# DB roli (faqat o'qish)
psql "$BM_DB_DSN" -Atc "SELECT current_user, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user;"
psql "$BM_DB_DSN" -Atc "SELECT schemaname, tablename, rowsecurity FROM pg_tables WHERE schemaname='public' AND rowsecurity=false;"
psql "$BM_DB_DSN" -Atc "SELECT polname, polrelid::regclass FROM pg_policy;"
```

Natijalarni baholash mezonlari:

- `ss`: faqat kerakli portlar (80/443, SSH cheklangan, 8081/6808/6107 faqat zarur
  bo'lsa); DB porti (5432) tashqariga ochiq bo'lmasligi.
- SSH: `passwordauthentication no`, root login taqiqlangan yoki kalit bilan.
- fail2ban: sshd jail aktiv.
- `.env` va `state/`: 600/700; guruh/other o'qishi yo'q.
- DB roli: `rolsuper=f`, `rolbypassrls=f` — 2026-10-29 da tasdiqlandi (SEC-07 §2.1).
- RLS: 29 jadvalda `rowsecurity=t`, policy 0 — **qabul qilingan qaror** (§2.1):
  tenant izolyatsiyasi ilova qavatida; policy-asosidagi RLS alohida `bm_app`
  roli bilan kelajakdagi ish.

---

## 5. Deploy rejasi (ruxsatdan keyin) va rollback

### 5.1 Tayyorgarlik

1. VDS'da backup katalog: `BACKUP=/opt/bm-backups/$(date +%Y%m%d-%H%M%S)`.
2. O'zgaradigan fayllar nusxasi: `data.py`, `admin.py`, `fastapi_app.py`,
   `system.py`, `deps.py`, `web/index.html`, `gps/api.py`.
3. `md5sum` oldindan yozib olinadi.

### 5.2 Chiqarish tartibi

1. Avval `index.html` (statik) — `scp -O`, md5 solishtirish.
2. Keyin Python fayllari — `scp -O`.
3. `python -m py_compile` VDS'da har bir fayl uchun.
4. `systemctl restart bm-dashboard`; `bm-dashboard` GPS threadlari sabab to'xtashi
   cho'zilishi mumkin — jarayon yakunini kutish, keyin `systemctl is-active`.
5. `systemctl restart bm-gps` (FAQAT SEC-05 chiqarilganda).
6. `journalctl -u bm-dashboard -n 100 --no-pager` — traceback yo'qligi.
7. Smoke test (brauzer/curl, tokensiz):
   - login → `/api/me` 200;
   - `/api/settings/companies` begona kompaniya creds → 403;
   - GET `/api/backup/run` → 404/405, POST MANAGER → 403;
   - cross-site Origin POST → 403;
   - GPS: tashqi IP'dan token bilan 200, tokensiz 401.
8. 24 soat kuzatish: 401/403 spike, 5xx, audit log oqimi.

### 5.3 Rollback (muammo bo'lsa)

1. Backup katalogdan fayllarni qaytarish; `md5sum` tekshirish.
2. `systemctl restart bm-dashboard` (va kerak bo'lsa `bm-gps`).
3. Smoke test: login + asosiy sahifalar.
4. Sabab tahlili lokalda; qayta chiqarishga shoshilmaslik.

Rollback **ma'lumotlarga tegmaydi** (faqat kod/HTML), shuning uchun DB restore
talab qilinmaydi.

---

## 6. Tekshiruv buyruqlari (lokal)

```powershell
# Sintaksis
& "C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe" -X utf8 -m py_compile `
  bm_automation/app/dashboard/routers/data.py `
  bm_automation/app/dashboard/routers/admin.py `
  bm_automation/app/dashboard/fastapi_app.py `
  bm_automation/app/dashboard/routers/system.py `
  bm_automation/app/dashboard/deps.py `
  bm_automation/app/gps/api.py

# Yangi xavfsizlik testlari
& "C:\...\python.exe" -X utf8 -m pytest tests/test_security_hardening.py -q

# Tenant/dashboard regressiya
& "C:\...\python.exe" -X utf8 -m pytest tests/test_tenant_context.py tests/test_dashboard_users.py -q

# To'liq to'plam (repo ildizidan)
$env:BM_DB_DRIVER="sqlite"; $env:BM_TEST_MODE="1"
& "C:\...\python.exe" -X utf8 -m pytest tests/ -q
```

Natijalar (2026-10-29, SEC-09/10/13 + MFA bosqichi):

- `py_compile` (`alerts.py`, `totp.py`, `auth.py`, `deps.py`, `fastapi_app.py`,
  `schema.py`, `storage.py`, `backup.py`) — OK
- `node --check` (index.html 3 inline blok) — OK; MFA funksiyalari/ID'lar
  cross-check — OK
- `tests/test_mfa_and_alerts.py` — **16 passed** (TOTP RFC vektorlari, alertlar,
  MFA API, reset ruxsatlari, CSP)
- `tests/test_dashboard.py` + `tests/test_security_hardening.py` — **111 passed**
- Paket pinlari PyPI'da mavjudligi tekshirildi (15/15 BOR)
- To'liq to'plam — **1584 passed, 0 failed** (17 daqiqa, exit 0;
  `state/_full_pytest_20261029_mfa.log`)

Natijalar (2026-10-29, oldingi bosqich):

- `py_compile` (70 o'zgargan modul) — OK
- `node --check` (index.html 3 blok, gps_ui.html 1 blok inline JS) — OK
- `tests/test_security_hardening.py` — **30 passed**
- Tenant/dashboard/MyAI regressiya to'plami — **267 passed**
- To'liq to'plam — **1568 passed, 0 failed** (13 daqiqa, exit 0;
  `state/_full_pytest_20261029.log`)

Deploy (2026-10-29, `bm-dashboard` + `bm-bot` restart):

- Backup: `/opt/bm/state/deploy_backup_20261029_205824_hardening`
  (`before_runtime.tar.gz`, `before_vault.tar.gz`, `md5_before.txt`,
  `.env`/`profiles.json`/`tokens.json` md5 — o'zgarmagan).
- 13 fayl (12 py + 2 html, jami 13 yo'l) + `knowledge_vault` qaytalari;
  lokal==VDS md5 100%.
- Smoke: `/`=200, `/api/me`=200 (tokenda), `/api/health` anonim 401 /
  MANAGER+ 200, `Host: evil.example`=400, begona `Origin` POST=403,
  `Sec-Fetch-Site: cross-site`=403, GPS begona Origin'ga ACAO yo'q /
  same-origin bor, `Referrer-Policy: no-referrer`, `/api/video/jt-streams`
  tokensiz 401, `dashboard_sessions.json` 600, journal traceback 0,
  NRestarts 0, timer'lar active.

---

## 7. Qabul mezonlari (Definition of Done)

- [x] SEC-01…SEC-05 productionda; smoke testlar o'tdi (2026-10-29).
- [x] SEC-07 DB roli tekshirildi; RLS qarori hujjatlashtirildi (§2.1).
- [x] `.env`/`state/` ruxsatlari 600/700 (2026-10-08 hardening).
- [x] Paketlar `==` bilan qulflandi; CI action'lari commit SHA'da (SEC-09).
- [x] Audit alerting: login.fail (≥5/10 daq.) va backup PARTIAL/ERROR (SEC-13).
- [x] CSP: `base-uri`/`form-action`/`object-src`/`frame-ancestors` (SEC-10).
- [x] Ixtiyoriy MFA (TOTP): setup/enable/disable + login kod bosqichi + admin reset.
- [ ] `pip-compile --generate-hashes` + `--require-hashes` (P1, keyingi qadam).
- [ ] `bm_app` DML roli + policy-asosidagi RLS (P1, alohida ish).
- [ ] Rollback bir marta mashq qilindi (staging yoki oynada).
