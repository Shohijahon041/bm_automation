# Deploy Prompt

## Maqsad
VDS ga deploy qilish (Docker + PostgreSQL) va yangilash.

## VDS Ma'lumotlari
- **Server**: `vps12327.eskiz.uz` (189.74.100.25)
- **User**: `root`
- **SSH Key**: `~/.ssh/bm_vps_ed25519`
- **Dashboard path**: `/opt/bm/bm_automation/app/dashboard/web/`
- **Service**: `bm-dashboard.service` (uvicorn, port 8080)
- **Nginx**: 443 proxy → 127.0.0.1:8080
- **Live URL**: `https://bmbrutto.uz/`

## Deploy Bosqichlari

### 1. Local tayyorlash
```bash
# Syntax check
cd bm_automation/app/dashboard/web
python -c "
import re
html = open('index.html', encoding='utf-8').read()
scripts = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', html, re.S)
for i,s in enumerate(scripts):
    open(f'script_{i}.js','w',encoding='utf-8').write(s)
"
node --check script_0.js && node --check script_1.js && node --check script_2.js && node --check script_3.js
rm script_*.js

# Test
cd ../../../../
python -X utf8 -m pytest tests/test_dashboard*.py -q
```

### 2. SCP yuborish
```bash
cd bm_automation/app/dashboard/web
md5sum index.html
scp -i ~/.ssh/bm_vps_ed25519 index.html root@189.74.100.25:/opt/bm/bm_automation/app/dashboard/web/index.html
```

### 3. Serverda tekshirish
```bash
ssh -i ~/.ssh/bm_vps_ed25519 root@189.74.100.25 "
    md5sum /opt/bm/bm_automation/app/dashboard/web/index.html
    systemctl restart bm-dashboard.service
    sleep 3
    systemctl is-active bm-dashboard.service
    ss -tlnp | grep 8080
"
```

### 4. Live tekshirish
```bash
curl -s -o /dev/null -w "HTTP %{http_code} size=%{size_download}\n" https://bmbrutto.uz/
```

## Docker Deploy (To'liq tizim uchun)

```bash
# Serverda
cd /opt/bm_loyiha
git pull  # yoki yangi ZIP
docker compose up -d --build

# Holat
docker compose ps
docker compose logs -f bot
docker compose logs -f dashboard
```

## Backup (Serverda)

```bash
# Manual backup
docker compose exec -T db pg_dump -U bm -d bm_loyiha | gzip > /root/backups/bm_loyiha_$(date +%F).sql.gz

# Cron (har kuni 03:00)
crontab -e
0 3 * * * cd /opt/bm_loyiha && docker compose exec -T db pg_dump -U bm -d bm_loyiha | gzip > /root/backups/bm_loyiha_$(date +\%F).sql.gz
30 3 * * * find /root/backups -name "bm_loyiha_*.sql.gz" -mtime +14 -delete
```

## Muhim Eslatmalar

1. **`.env`** — serverda alohida, gitga yo'q
2. **StaticFiles** — `index.html` har so'rovda diskdan o'qiladi, **restart shart emas** (lekin service restart etsa ham bo'ladi)
3. **md5** — local va remote md5 mos kelishi kerak
4. **Logs** — `docker compose logs -f dashboard` / `journalctl -u bm-dashboard -f`