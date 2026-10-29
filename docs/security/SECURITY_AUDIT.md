# BM Automation — xavfsizlik auditi

Sana: 2026-10-29
Branch: `audit/jt1078-parser-fix`
Rejim: **faqat lokal, o'qish + lokal o'zgarishlar**. Production serverga ulanilmadi,
deploy/restart/firewall/DNS o'zgartirilmadi, production DB'da amal bajarilmadi.
Real mijoz ma'lumoti test uchun ishlatilmadi (barcha testlar sintetik: tmp SQLite +
tmp `profiles.json`).

---

## 1. Qamrov va cheklovlar

| Yo'nalish | Ko'rilgan | Cheklov |
|---|---|---|
| Web/API (OWASP Top 10:2025) | FastAPI routerlar, `deps.py`, `fastapi_app.py`, `web/index.html` | — |
| Multi-tenant | `Scope`, `force_route`, `tenant_guard`, `metrics`, `/api/salary`, admin company amallari | — |
| PostgreSQL | DSN/sslmode, pool, RLS, so'rov parametrlari | DB role huquqlari productionda tekshirilmadi |
| Backup/recovery | `db/backup.py`, `config/settings.py`, `system.py` | Restore sinovi izolatsiyada ham bajarilmadi (DSN yo'q) |
| Dependency/supply chain | `requirements.txt`, `Dockerfile`, `.github/workflows/test.yml` | Internetdan CVE skani bajarilmadi |
| Logging/monitoring | `utils/logger.py`, `audit_event`, alerting | Production log fayllari ko'rilmadi |
| Server (SSH/firewall/OS) | Repo ichidagi dalillar | **Production tekshiruvi taqiqlangan** — 10-bo'limga qarang |

Muhim: quyida "tekshirilgan" deb belgilangan narsa faqat kod/fayl daliliga asoslanadi.
Production holati (portlar, role huquqlari, fayl ruxsatlari) **tekshirilmagan** va
hisobotda mavjud deb ko'rsatilmagan.

---

## 2. Umumiy baho

- Autentifikatsiya va sessiya mexanizmi puxta: PBKDF2-SHA256, HttpOnly+SameSite=Lax
  cookie, 12 soatlik siljiydigan sessiya + 7 kun mutlaq chegara, 8 xato/15 daqiqa
  lockout, parol o'zgarganda boshqa sessiyalarni yopish, audit loglar.
- Rol matritsasi va tenant scope serverda majburiy (`Scope`, `force_route`,
  `tenant_guard` fail-closed) — UI'da tugmani yashirishga tayanmaydi.
- Kod bo'yicha 5 ta aniq zaiflik topildi va **lokalda tuzatildi** (SEC-01…SEC-05).
  Yana 9 ta o'rta/past riskli kamchilik hujjatlashtirildi (SEC-06…SEC-14).
- Eng katta tizimli risk: **tenant izolyatsiyasi faqat ilova darajasida** — DB'da RLS
  yoqilgan, lekin policy yo'q (SEC-07). Ilovadagi bitta scope xatosi butun bazani
  ochadi; shuning uchun regressiya testlari (`tests/test_security_hardening.py`)
  doimiy ishlatilishi shart.

---

## 3. Zaifliklar jadvali

Severity: Critical > High > Medium > Low. "Holat" — local kod holati, productionga
chiqarilmagan.

| ID | Severity | Zaiflik | Dalil | Holat |
|---|---|---|---|---|
| SEC-01 | High | `/api/salary` bo'sh yo'nalish qamrovida butun bazani eksport qilardi (fail-open) | `routers/data.py:244-264` | **Tuzatildi** (403 fail-closed) |
| SEC-02 | High | Har qanday ADMIN boshqa korxona kredensialini o'zgartirishi/o'chirishi mumkin edi | `routers/admin.py:339-350, 371-382, 415-450, 1416-1430` | **Tuzatildi** (super/own-company guard) |
| SEC-03 | High | Non-super ADMIN `BM_BASE_URL`/`BM_USERNAME`ni o'zgartirib kredensialni begona serverga yuborishi mumkin edi | `routers/admin.py:286-297, 304-336` | **Tuzatildi** (kritik kalitlar super-only) |
| SEC-04 | High | CSRF himoyasi yo'q edi; `GET /api/backup/run` holat o'zgartirardi | `fastapi_app.py:116-145`, `routers/system.py:296-302` | **Tuzatildi** (Origin/Sec-Fetch-Site + POST) |
| SEC-05 | High | GPS API token bo'sh bo'lsa `0.0.0.0`da autentifikatsiyasiz ochiq; token `==` bilan solishtirilardi | `gps/api.py:55-86` | **Tuzatildi** (faqat loopback + `compare_digest`) |
| SEC-06 | Medium | GPS API har qanday `Origin`ni aks ettiradi + `Allow-Credentials: true`; UI tokeni URL'da, `Referrer-Policy` yo'q | `gps/api.py:189-224, 444-450` | **Tuzatildi** (CORS whitelist + `no-referrer`) |
| SEC-07 | Medium | RLS barcha jadvallarda yoqilgan, lekin **policy yo'q** → ilova roli RLS'ni chetlab o'tishi shart; izolyatsiya faqat app-level | `db/schema.py:592-594` | Ochiq (dizayn qarori kerak) |
| SEC-08 | Medium | Sessiya tokenlari `state/dashboard_sessions.json`da ochiq matnda; statik `DASHBOARD_TOKEN` `==` bilan solishtiriladi | `deps.py:150-190, 776-784` | **Tuzatildi** (fayl 0600, `compare_digest`) |
| SEC-09 | Medium | Bog'liqliklar pastki chegara bilan pin qilinmagan, lockfile/hash yo'q; Docker root + mutable tag; CI action'lar SHA'ga pin qilinmagan | `requirements.txt`, `Dockerfile:3,26-28`, `.github/workflows/test.yml:15,19` | Ochiq |
| SEC-10 | Low | CSP `script-src 'unsafe-inline'` — XSS himoyasi zaiflashgan | `fastapi_app.py:107` | Ochiq |
| SEC-11 | Low | `/api/health` har qanday autentifikatsiyalangan foydalanuvchiga host/disk/xotira ma'lumotini beradi | `routers/system.py` (`/api/health`) | **Tuzatildi** (MANAGER+) |
| SEC-12 | Low | PBKDF2-SHA256 120k iteratsiya (OWASP tavsiyasi 600k); ADMIN uchun MFA yo'q | `deps.py:357-371` | **Tuzatildi** (600k + auto-rehash); MFA — ochiq |
| SEC-13 | Low | Audit log yoziladi, lekin alerting/paging yo'q (faqat fayl/konsol) | `deps.py:866-879`, `utils/logger.py` | Ochiq |
| SEC-14 | Low | `/api/settings/companies` ro'yxati non-super ADMIN'ga barcha korxonalar nomini ko'rsatadi; `TrustedHostMiddleware` yo'q | `routers/admin.py:353-360` | **Tuzatildi** (own-company filtri + Host guard) |

**Tuzatilmagan, lekin tekshirilgan himoyalar** (auditda zaiflik topilmadi):

- SQL injection: qiymatlar parametrlangan (`db/base.py`, `storage.py`); f-string faqat
  ichki/whitelist jadval-ustun nomlari uchun. MyAI tool'lari qat'iy so'rovlar.
- XSS (ko'rilgan yo'llar): `esc()`/`escAttr()` izchil, markdown preview escape
  qilinadi; SEC-10 — himoya chuqurligi masalasi, aniq sink topilmadi.
- IDOR: API'lar `Scope` + `force_route` + `tenant_guard` orqali serverda cheklanadi;
  begona kompaniya/bloklangan tenant 403.
- Parol siyosati, login brute-force lockout, sessiya revokatsiyasi, audit logda
  parol/token yozilmasligi — mavjud va testlar bilan qoplangan.

---

## 4. SEC-01 — `/api/salary` fail-open (tuzatildi)

**Muammo.** `scope_routes()` bo'sh to'plam qaytarsa (`routes=[]`), `route` parametri
bo'sh satr bo'lib qolardi va `salary_export(..., route="")` **barcha korxonalar**
haydovchilari bo'yicha XLSX yasardi. Ya'ni yo'nalishi biriktirilmagan (yoki
profiles.json'da yo'nalishi yo'q) kompaniya foydalanuvchisi butun bazani yuklab olardi.

**Dalil.** `bm_automation/app/dashboard/routers/data.py:253-258` (tuzatishdan oldin
`if scopes is not None and not scopes:` bloki yo'q edi).

**Ta'sir.** Cross-tenant ma'lumot oqishi (ish haqi/ km / haydovchi ma'lumotlari) —
bir marta eksport bilan barcha mijozlar.

**Tuzatish.** Bo'sh qamrovda 403:

```python
scopes = scope_routes(scope)
if scopes is not None and not scopes:
    raise HTTPException(403, "Kompaniyaga yo'nalish biriktirilmagan")
```

**Test.** `tests/test_security_hardening.py::test_salary_fail_closed_without_routes`
(CTEST — yo'nalishsiz) va `test_salary_allowed_for_positive_scope` (ATEST — 200).

---

## 5. SEC-02 — kompaniya sozlamalarida cross-tenant amallar (tuzatildi)

**Muammo.** `/api/settings/companies` rol bo'yicha faqat ADMIN (rank 4) talab qilardi,
lekin **qaysi korxona** ustida ishlayotganini tekshirmasdi. `company="ATEST"` bo'lgan
ADMIN `BTEST`ning BM login/parolini almashtirishi, yo'nalish qo'shishi, guruhlarini
o'zgartirishi yoki korxonani o'chirishi mumkin edi.

**Dalil.** `routers/admin.py:363-500` (`update_companies`), `1416-1430`
(`_update_company_creds`) — tuzatishdan oldin `_company_in_scope` chaqiruvi yo'q edi.

**Ta'sir.** Boshqa mijozning BM akkaunti egallanishi (kredensial almashishi), uning
yo'nalishlari/guruhlarini o'zgartirish, soft-delete orqali xizmatni to'xtatish.

**Tuzatish.** `_company_in_scope()` guard `add`, `groups`, `delete`, `update` va
`creds` amallariga qo'shildi; super bo'lmagan ADMIN faqat o'z kompaniyasi bilan
ishlaydi. Rad etish audit logga yoziladi (`_deny_company`).

**Testlar.**
`test_non_super_admin_cannot_change_other_company_creds`,
`test_non_super_admin_cannot_delete_or_add_company`,
`test_non_super_admin_can_change_own_company_creds`,
`test_super_admin_can_touch_any_company`.

---

## 6. SEC-03 — `.env` orqali kredensial yo'naltirish (tuzatildi)

**Muammo.** `/api/settings/env` non-super ADMIN'ga `BM_BASE_URL` va `BM_USERNAME`ni
o'zgartirishga ruxsat berardi. Bu bilan BM login/parolini **begona serverga**
yuborish yoki boshqa korxona ma'lumotini tortish mumkin edi (keyingi restartda
kuchga kiradi).

**Dalil.** `routers/admin.py:286-297` — `_CRITICAL_ENV_KEYS` ro'yxati.

**Tuzatish.** `BM_BASE_URL`, `BM_USERNAME` (va boshqa maxfiy/integratsion kalitlar)
super ADMIN'ga yopildi. Non-critical sozlamalar (masalan `OPENROUTER_MODEL`) ADMIN
uchun ochiq qoldi.

**Testlar.** `test_env_bm_base_url_super_only` (non-super rad, super ruxsat, tmp
`.env`ga yoziladi — haqiqiy `.env`ga tegilmaydi).

---

## 7. SEC-04 — CSRF (tuzatildi)

**Muammo.** Cookie bilan autentifikatsiya qilinadigan POST endpointlarda CSRF token
ham, Origin tekshiruvi ham yo'q edi. `SameSite=Lax` ko'p holatni to'sadi, lekin
top-level navigatsiya bilan ishlaydigan **GET** amali (`/api/backup/run`) begona
saytdan (`<img>`, havola) ishga tushirilishi mumkin edi.

**Dalil.** `fastapi_app.py:147-181` (middleware tartibi), `routers/system.py:296`
(endi `@router.post`).

**Ta'sir.** Backup'ni takroriy ishga tushirish (resurs sarfi, tashqi DSN'ga yozish),
boshqa mutatsiyalar brauzer orqali.

**Tuzatish.**
1. `csrf_origin_guard` middleware: `POST/PUT/PATCH/DELETE /api/*` uchun
   `Sec-Fetch-Site: cross-site` yoki ruxsatsiz `Origin` → 403.
2. `/api/backup/run` faqat POST; `_POST_ROLE_MIN["/api/backup"] = 4`.
3. Frontend `postJSON("/api/backup/run")` ishlatadi (`web/index.html:5986`).

**Testlar.** `test_csrf_blocks_cross_site_origin`, `test_csrf_blocks_sec_fetch_cross_site`,
`test_csrf_allows_same_origin`, `test_csrf_does_not_block_get`,
`test_backup_run_is_post_only`, `test_backup_run_requires_admin`,
`test_backup_run_requires_auth`.

---

## 8. SEC-05 — GPS API ochiq rejimi (tuzatildi)

**Muammo.** `GPS_API_TOKEN` bo'sh bo'lsa `_check_token` `True` qaytarardi, servis esa
`GPS_API_HOST=0.0.0.0`da tinglaydi (`gps/config.py:20-33`) — tarmoqdagi har kim
GPS/video ma'lumotini o'qishi va qurilmalarni boshqarishi mumkin edi. Token
solishtirish oddiy `==` bilan (timing side-channel).

**Tuzatish.** Token bo'sh bo'lsa API **faqat loopback**dan ochiq
(`_client_is_local`), aks holda 401. Barcha solishtirishlar `hmac.compare_digest`.
`bm_session` cookie orqali kirish saqlanib qoldi (dashboard foydalanuvchisi).

**Testlar.** `test_gps_empty_token_loopback_only`,
`test_gps_token_bearer_cookie_query`.

---

## 9. Ochiq kamchiliklar (tavsiyalar)

### SEC-06 (Medium) — GPS API CORS va URL tokeni
`gps/api.py:199-208` har qanday `Origin`ni aks ettiradi va
`Access-Control-Allow-Credentials: true` qo'yadi. Hozir `SameSite=Lax` cookie'ni
cross-site XHR'da yubormaydi, lekin bu himoya bitta sozlamaga bog'liq; `?token=`
URL'da bo'lgani uchun `Referrer` orqali uchinchi tomon xarita provayderiga oqishi
mumkin. Tavsiya: dashboard origin'ini whitelist qilish, `Referrer-Policy: no-referrer`,
token uchun qisqa muddatli imzolangan cookie.

### SEC-07 (Medium) — RLS policy yo'q
`schema.py:592-594` faqat `ENABLE ROW LEVEL SECURITY` bajaradi. Policy bo'lmasa va
ilova roli jadval egasi/superuser bo'lsa, RLS hech narsani cheklamaydi — izolyatsiya
`Scope`/`force_route`ga tayanadi. Tavsiya (defense-in-depth): har jadvalga
`company_id = current_setting('app.company_id')::int` policy'lari, ilova ulanishida
`SET LOCAL app.company_id`, alohida migratsiya roli. Production DB rolini tekshirish
kerak (superuser/BYPASSRLS emasligi).

### SEC-08 (Medium) — sessiya tokenlari diskda
`state/dashboard_sessions.json` ochiq matnda (token = to'liq kirish). Tavsiya: fayl
ruxsati 0600/OS ACL, yoki token xeshini saqlash. `DASHBOARD_TOKEN` solishtiruvi ham
`hmac.compare_digest`ga o'tkazilsin.

### SEC-09 (Medium) — supply chain
`requirements.txt` faqat `>=`; lockfile yo'q. Docker `python:3.12-slim` mutable tag,
`USER` yo'q (root). CI `actions/checkout@v4`, `setup-python@v5` SHA emas. Tavsiya:
`pip-compile --generate-hashes`, `pip install --require-hashes`, non-root `USER`,
action'larni commit SHA'ga pin qilish. **Paketlar avtomatik yangilanmaydi** — versiya
yangilash alohida, test bilan rejalashtiriladi (`HARDENING_PLAN.md`).

### SEC-10…SEC-14 (Low)
CSP `'unsafe-inline'`, `/api/health` info disclosure, PBKDF2 120k, alerting yo'qligi,
kompaniyalar ro'yxati ko'rinishi va `TrustedHostMiddleware` yo'qligi — batafsil
tavsiyalar `HARDENING_PLAN.md`da.

---

## 10. Production server — tekshirilmagan (ruxsat kerak)

Quyidagilar **tekshirilmadi** (productionga ulanish taqiqlangan): ochiq portlar,
SSH konfiguratsiyasi, firewall, fail2ban, OS yangilanishlari, servis izolyatsiyasi,
`.env`/`state/` fayl ruxsatlari, DB roli huquqlari, backup fayllari va ularning
ruxsatlari, log rotatsiyasi. Avvalgi audit (2026-10-08) bo'yicha SSH keys-only,
fail2ban va nginx TLS qayd etilgan — bu **joriy holat tasdiqi emas**.

Tekshirish buyruqlari `HARDENING_PLAN.md` 4-bo'limida (faqat o'qish, alohida ruxsat
bilan bajariladi).

---

## 11. Lokal tekshiruv natijalari

| Tekshiruv | Buyruq | Natija |
|---|---|---|
| Sintaksis | `py_compile` (o'zgargan 6 modul + 2 test) | OK |
| Inline JS | `node --check` (index.html, 3 blok) | OK |
| Yangi xavfsizlik testlari | `pytest tests/test_security_hardening.py -q` | **20 passed** |
| Tenant/dashboard regressiya | `pytest tests/test_tenant_context.py tests/test_dashboard_users.py -q` | **130 passed** |
| To'liq to'plam | `pytest tests/ -q` | **1549 passed, 0 failed** (50 daqiqa, exit 0; log: `state/_security_full_pytest.log`) |
