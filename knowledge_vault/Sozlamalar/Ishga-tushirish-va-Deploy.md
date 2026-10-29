# Ishga tushirish va deploy

Production — **native VDS**, Docker emas, git deploy ham emas. Kod lokal
repodan fayl ko'chirish bilan yangilanadi.

## Server

| Parametr | Qiymat |
|---|---|
| OS | Ubuntu 24.04 |
| Manzil | `bmbrutto.uz` |
| SSH | `ssh vps12327.eskiz.uz` (kalit bilan, root) |
| Loyiha | `/opt/bm`, foydalanuvchi `bm` |
| Venv | `/opt/bm/.venv` (Python 3.12) |
| Baza | PostgreSQL 17, `127.0.0.1:5432` |
| nginx | 80/443; `/gps/`, `/api/gps/`, `/api/video/`, `/hls/` → ichki 8081 |
| Zaxira | `/etc/cron.d/bm-backup` → `/root/backups/` |

### Xizmatlar

| Xizmat | Vazifa |
|---|---|
| `bm-bot.service` | Telegram polling |
| `bm-dashboard.service` | uvicorn `127.0.0.1:8080` + GPS threadlari |
| `bm-daily.timer` | 20:00 — kunlik jadval |
| `bm-schedule.timer` | 22:00 — kunlik hisobot + DB sync |
| `bm-token-refresh.timer` | 06:30/18:30 — BM keep-alive |

## Deploy tartibi

### 1. Lokalda tekshirish

```bash
python -X utf8 -m py_compile <o'zgargan .py fayllar>
python -X utf8 -m pytest tests/ -q
# index.html o'zgargan bo'lsa inline JS: node --check
```

### 2. Serverdagi nusxa bilan solishtirish

O'zgargan fayllarni `/tmp`ga olib, lokal bilan `diff` qilinadi — serverda
faqat mahalliy bo'lmagan patchlar bo'lmasligi kerak.

### 3. Zaxira

```bash
B=/opt/bm/state/deploy_backup_$(date +%Y%m%d)_<nom>
mkdir -p $B
cd /opt/bm
tar czf $B/files_before.tar.gz <o'zgargan fayllar>
tar czf $B/vault_before.tar.gz knowledge_vault   # bilim bazasi tegilsa
md5sum <fayllar> > $B/md5_before.txt
```

### 4. Ko'chirish

Serverda SFTP o'chirilgan — **`scp -O`** (legacy) ishlatiladi:

```bash
scp -O <fayl> vps12327.eskiz.uz:/tmp/bm_deploy/
```

Keyin serverda:

```bash
cd /tmp/bm_deploy
/opt/bm/.venv/bin/python -m py_compile <py fayllar>
find bm_automation -type f | while read f; do
  install -o bm -g bm -m 644 "$f" "/opt/bm/$f"
done
```

- Papkalar `755`, fayllar `644`, egasi `bm:bm`.
- `.env`, `state/`, `profiles.json`, `tokens.json`, `reports/` **hech qachon**
  almashtirilmaydi.
- Bilim bazasi: serverdagi mavjud qaydlarni avval ro'yxatga olish; foydalanuvchi
  yozgan qayd ustidan yozilmasin.

### 5. Restart

```bash
systemctl restart bm-bot bm-dashboard
systemctl is-active bm-bot bm-dashboard
```

> Dashboard restart ~90 soniya cho'zilishi mumkin: GPS threadlari SIGTERM'ga
> darhol chiqmaydi. `is-active` `active` bo'lguncha kutish kerak. Restart
> paytida jonli video/Setka oqimlari uziladi va qayta ochiladi.

### 6. Tekshirish

```bash
journalctl -u bm-dashboard -u bm-bot --since "5 min ago" | grep -iE "traceback|exception"
systemctl show -p NRestarts --value bm-dashboard
md5sum <fayllar>                     # lokal bilan mos
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/api/<yangi>
curl -s https://bmbrutto.uz/ | grep -c "<yangi bo'lim nomi>"
stat -c "%U:%G %a" /opt/bm/bm_automation/profiles.json
```

Autentifikatsiya talab qiladigan funksiyani imkon bo'lsa super-admin sessiyasi
bilan haqiqiy o'qish/yozish/o'chirish orqali sinash kerak.

## Rollback

```bash
B=/opt/bm/state/deploy_backup_<sana>_<nom>
cd /opt/bm
tar xzf $B/files_before.tar.gz
systemctl restart bm-bot bm-dashboard
```

Fayl o'chirilgan bo'lsa — `git` emas, tar zaxiradan tiklanadi.

## Tez-tez uchraydigan tuzoqlar

| Muammo | Sabab | Yechim |
|---|---|---|
| Dashboard 502 | To'liq bo'lmagan deploy / import xatosi | `journalctl`, `py_compile`, yetishmagan faylni yuborish ([[vds-502-import]]) |
| Faqat bitta firma ko'rinadi | `profiles.json` root egaligi | `chown bm:bm` fayl **va** papka ([[profiles-ruxsat]]) |
| `ON CONFLICT` xatosi | Bazada yangi unikal indeks yo'q — `init_db` migratsiyalarni faqat bo'sh bazada bajaradi | Indeksni qo'lda yaratish; `profiles` 2026-10-26 da kompozitga o'tkazilgan ([[profiles-ruxsat]]) |
| `scp` "Connection closed" | SFTP o'chirilgan | `scp -O` |
| Bot javob bermaydi | Ikki poller / eski jarayon | Faqat VDS boti qolsin ([[telegram-conflict]]) |
| API 401 BM'da | Token eskirgan / BM tomonidagi o'zgarish | `keepalive`, tokenni lokal logindan yangilash ([[and-eco-token]]) |

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[vds-502-import]]
- [[profiles-ruxsat]]
- [[Arxitektura]]
- [[Xato-jurnali]]

<!-- avto-bog'liq:end -->
