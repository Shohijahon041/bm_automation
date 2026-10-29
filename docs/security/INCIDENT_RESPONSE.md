# BM Automation — hodisaga javob rejasi (Incident Response)

Sana: 2026-10-29
Amal qiladi: dashboard (FastAPI), GPS/video servis, bot, PostgreSQL, backup,
tashqi integratsiyalar (BM API, Telegram, Supabase/Neon).
Asosiy qoida: **avval to'xtatish va dalilni saqlash, keyin tiklash.** Hech qanday
buyruq token/parolni ekranga chiqarmaydi.

---

## 1. Rollar va aloqa

| Rol | Kim | Vazifa |
|---|---|---|
| Incident lead | Tizim egasi | Qaror, production ruxsati, mijozlarga xabar |
| Texnik | Dasturchi/sysadmin | Tekshiruv, containment, tiklash |
| Kuzatuvchi | Ikkinchi shaxs | Dalil, vaqt jadvali, postmortem |

Aloqa kanali: telefon + shaxsiy chat (public repo/issue'ga yozilmaydi).
Mijozlarga xabar: fakt + ta'sir + keyingi qadam; taxmin emas.

---

## 2. Birinchi 30 daqiqa (har qanday hodisa)

1. **Vaqt va dalil**: hodisa vaqti (UTC+5), aniqlagan shaxs, ko'rsatkichlar
   (log satri, IP, foydalanuvchi, endpoint) yoziladi.
2. **Dalilni saqlash**: log fayllar va `state/` nusxasi **o'chirilmasdan** alohida
   katalogga ko'chiriladi (hash bilan). Log rotatsiyasini to'xtatish shart emas —
   nusxa olish yetarli.
3. **Containment**: zararlangan akkaunt/token bloklanadi; kerak bo'lsa servis
   to'xtatiladi (faqat egasining ruxsati bilan).
4. **Aloqa**: incident lead xabardor qilinadi; taxminlar e'lon qilinmaydi.
5. **Yozib borish**: har bir amal vaqti bilan jurnalga yoziladi.

Severity:

- **S1** — cross-tenant ma'lumot oqishi, DB/kredensial egallanishi, to'liq xizmat
  to'xtashi.
- **S2** — bitta akkaunt egallanishi, GPS/video boshqaruvi, backup buzilishi.
- **S3** — cheklangan skan/brute-force, konfiguratsiya xatosi (ta'sirsiz).

---

## 3. Aniqlash manbalari

```bash
# Dashboard/bot loglari (fayl rotatsiyasi: logs/bm.log, 1MB x5)
grep -E "AUDIT action=(login\.fail|login\.blocked|tenant\.denied|password\.change|http\.mutation)" logs/bm.log | tail -100

# Servis jurnallari
journalctl -u bm-dashboard -u bm-bot -u bm-gps --since "24 hours ago" --no-pager | tail -200

# Xato/5xx
journalctl -u bm-dashboard --since "1 hour ago" --no-pager | grep -Ei "traceback|error|500"
```

Alert bo'lishi kerak (hozir SEC-13 — qo'lda): bir IP'dan `login.fail` ko'payishi,
`tenant.denied` seriyasi, `password.change`, backup `last_status=error`.

---

## 4. Playbook P1 — kredensial/token oshkor bo'ldi

Belgilar: `.env` qiymati chat/log/commit/issue'da ko'rindi; BM API 401 to'xtamaydi;
begona IP'dan muvaffaqiyatli login.

1. **Containment**
   - Repo PUBLIC — oshkor bo'lgan har qanday token **kompromat** deb hisoblanadi.
   - Ta'sirlangan integratsiya kalitini darhol bekor qilish (Telegram bot token →
     @BotFather `/revoke`; BM parol → BM'da almashtirish; SMS gateway; OpenRouter).
   - `.env`ni faqat egasi tahrirlaydi (qiymat chatga yozilmaydi).
2. **Eradication**
   - Yangi qiymatlar `.env`ga kiritiladi; bot va dashboard **restart** qilinadi
     (dotenv faqat startda o'qiladi).
   - `DASHBOARD_TOKEN` almashtirilsa: barcha `state/dashboard_sessions.json`
     sessiyalari bekor qilinadi (fayl tozalanadi yoki servis restart + sessiya
     faylini almashtirish), GPS `bm_token` cookie ham yangilanadi.
3. **Recovery/verify**
   - `journalctl`da 401/403 oqimi; BM sync ishlashi; backup holati.
   - Git tarixida qolgan sir → tarixni tozalash alohida qaror (force-push egasining
     ruxsati bilan); bundan oldin kalit allaqachon almashtirilgan bo'lishi shart.
4. **Xabar**: qaysi kalit, qachon, qanday ta'sir — aniq.

---

## 5. Playbook P2 — sessiya/akkaunt egallandi

Belgilar: notanish IP'dan `/api/me` yoki mutatsiyalar; `AUDIT ... user=<nom>`.

1. Akkauntni bloklash: Settings → Dashboard foydalanuvchilari → `active=0`
   (yoki DB'da `dashboard_user_update(uid, active=0)`).
2. `drop_user_sessions(username)` — barcha sessiyalar yopiladi.
3. Parol almashtirish (kuchli, boshqa qurilma sessiyalari yopiladi).
4. Agar ADMIN bo'lsa: `DASHBOARD_ADMIN_PASS` almashtiriladi, servis restart.
5. Tekshirish: audit logda o'sha akkauntning barcha mutatsiyalari ro'yxati;
   o'zgartirilgan kompaniya kredensiallari/guruhlari qaytariladi.
6. Sabab: phishing, qayta ishlatilgan parol, ochiq token — postmortemda.

---

## 6. Playbook P3 — cross-tenant ma'lumot oqishi

Belgilar: foydalanuvchi boshqa korxona ma'lumotini ko'rgani haqida xabar;
`/api/salary`, `/api/settings/companies`, `/api/electricity` kabi endpointlar.

1. **Darhol**: ta'sirlangan endpointni vaqtincha yopish (nginx'da 403 yoki servis
   sozlamasi) — faqat egasining ruxsati bilan.
2. Qaysi foydalanuvchi, qaysi korxona, qaysi vaqt oralig'i — audit logdan aniqlash
   (`AUDIT action=http.mutation`, `tenant.denied`).
3. Eksport qilingan fayllar (XLSX) bor-yo'qligini aniqlash; mijozga xabar berish
   qarori (shartnoma/ huquqiy talab).
4. Tuzatish: scope tekshiruvi (SEC-01/SEC-02 regressiya testlari), keyin smoke test.
5. Postmortem: qaysi qatlam (router/service/export) va nima uchun test tutmagan.

---

## 7. Playbook P4 — GPS/video qurilma egallanishi

Belgilar: begona IMEI ro'yxatga tushgan; stream boshqaruvi (start/stop) begona
tomondan; qurilma sozlamalari o'zgargan.

1. `GPS_API_TOKEN` (DASHBOARD_TOKEN) almashtirish + `bm-gps` restart.
2. Qurilmalarni tekshirish: `storage`/UI orqali bog'lanishlar, `enabled` holati,
   `kind`; notanish IMEI'ni bloklash (`set_device_enabled(imei, False)`).
3. JT808/JT1078 portlariga tashqi kirishni cheklash (faqat operator IP'lari) —
   firewall o'zgarishi egasining ruxsati bilan.
4. Video arxiv/playback fayllariga begona kirish izlari (log) tekshiriladi.
5. SEC-06 (CORS/URL token) tuzatilgach qayta ko'rib chiqish.

---

## 8. Playbook P5 — DB buzilishi / ransomware

1. **Darhol**: dashboard/botni to'xtatish (yozishni kesish), DB ulanishini yopish.
2. Dalil: `pg_stat_activity`, `pg_stat_statements` (agar bor), DB loglari; disk
   snapshot (agar mavjud).
3. Tiklash: backup (Supabase/Neon) holatini tekshirish; **restore faqat izolatsiyada**
   (yangi DB instansiya) bajariladi, production DB ustiga yozilmaydi.
4. DB rolini almashtirish: yangi parol, kerak bo'lsa yangi rol; `.env` yangilanadi.
5. Tekshirish: yozuvlar soni, oxirgi sana, foydalanuvchilar; cross-tenant
   yaxlitlik (`company_id` bo'sh yozuvlar).
6. Mijozlarga xabar: qaysi davr, qanday ma'lumot, nima tiklandi.

Eslatma: hozirgi backup — **DB→DB sinxronizatsiya** (`db/backup.py`), fayl dumps
emas. Restore hujjati va sinovi yo'q — bu P1 ish (`HARDENING_PLAN.md`).

---

## 9. Playbook P6 — supply chain (paket/CI)

Belgilar: CI'da notanish o'zgarish; `pip install` yangi versiya; Docker image
kutilmaganda o'zgargan.

1. Build'ni to'xtatish; `requirements.txt` va lockfile diffini tekshirish.
2. Shubhali paketni olib tashlash, avvalgi ma'lum versiyaga qaytish.
3. CI action'larni SHA'ga pin qilish; tokenlarni CI secret'dan almashtirish.
4. Image'ni qayta qurish va `docker history` bilan tekshirish.
5. Postmortem: qaysi nazorat yo'q edi (hash, pin, review).

---

## 10. Dalil va maxfiylik qoidalari

- Log/token/parol **chatga, ticketga, commitga** yozilmaydi.
- Nusxa olishda hash: `sha256sum <fayl>`; nusxa read-only katalogda.
- Skrinshotlarda token/URL query ko'rinmasin (kesish).
- `state/dashboard_sessions.json`, `profiles.json`, `tokens.json`, `.env` — maxfiy;
  faqat egasi ochadi.
- Hodisa yopilgach: vaqt jadvali, sabab, tuzatish, oldini olish choralari
  `docs/security/`ga qo'shiladi (sirsiz).

---

## 11. Tekshirish ro'yxati (hodisa yopilishida)

- [ ] Tokenlar/parollar almashtirildi va eski qiymatlar bekor qilindi.
- [ ] Sessiyalar tozalandi; notanish qurilmalar chiqarildi.
- [ ] Audit logda shubhali amallar qolmadi (yoki hujjatlashtirildi).
- [ ] Servislar `active`; smoke test (login, asosiy sahifa, GPS, backup).
- [ ] Backup holati `ok`; restore rejasi yangilandi.
- [ ] Mijoz xabari yuborildi (agar talab bo'lsa).
- [ ] Regressiya testi qo'shildi (hodisa sababi qaytmasligi uchun).
