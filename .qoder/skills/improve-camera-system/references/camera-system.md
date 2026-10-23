# BM kamera tizimi

## Asosiy fayllar

- `bm_automation/app/gps/gps_ui.html` — GPS, Video, Setka, player va browser interaction.
- `bm_automation/app/gps/api.py` — GPS/video API, qurilmalar, live/playback amallari.
- `bm_automation/app/gps/camera.py` — kamera buyruqlari va sessiyalar.
- `bm_automation/app/gps/jt1078.py` — media paketlari, stream va muxer lifecycle.
- `bm_automation/app/gps/storage.py` — qurilma/avtobus bog‘lanishi va counterlar.
- `bm_automation/app/gps/config.py` — GPS/video sozlamalari.
- `bm_automation/app/dashboard/web/index.html` — dashboard shell va GPS/Video embed.
- `tests/` — GPS, video, API va dashboard regressiya testlari.

Avval symbol va endpointlarni qidirish; nomlar o‘zgargan bo‘lsa shu ro‘yxatni mutlaq deb qabul qilmaslik.

## Runtime

- Kamera quyi tizimi `bm-dashboard` jarayoni ichida ishlaydi.
- Dashboard: uvicorn, lokal `127.0.0.1:8080`.
- ZLMediaKit jonli streamni browserga HTTP-FLV/HLS orqali yetkazadi.
- Qurilma signaling JT808, media JT1078 orqali keladi.
- VDS kod joyi `/opt/bm`; virtual muhit `/opt/bm/.venv`.
- Deploy git pull bilan emas, lokal fayllarni `scp -O` orqali ko‘chirish bilan bajariladi.

## Qabul mezonlari

### Bitta kamera

- Online holati aniq ko‘rinadi.
- Boshlash tugmasi 44×44 px dan kichik emas va loading holatini ko‘rsatadi.
- Video oqimi playerda ochiladi; console’da fatal error yo‘q.
- Snapshot real kadr beradi.
- Fullscreen ishlaydi va qaytganda sessiya saqlanadi.
- To‘xtatish media jarayonini yopadi.

### Setka

- Mavjud Video player logikasi qayta ishlatiladi.
- Online kameralar yuqorida, offline qurilmalar pastda.
- **Barchasini boshlash** faqat ishga tushishi mumkin bo‘lgan kanallarni boshlaydi.
- Bir kameradagi xato qolgan playerlarni buzmaydi.
- Bir sahifada keraksiz duplicate player/muxer yaratilmaydi.

### Xavfsizlik

- Login qilmagan so‘rov rad etiladi.
- Video ruxsati bo‘lmagan foydalanuvchi API orqali ham kira olmaydi.
- Boshqa kompaniya qurilmasi server tomonida bloklanadi.
- Noto‘g‘ri device/channel qiymati 4xx qaytaradi.
- Token yoki secret HTML, log, exception, screenshot va terminal chiqishida ko‘rinmaydi.

### Barqarorlik

- Ketma-ket start/stop race yaratmaydi.
- Offline qurilmada aniq xato va retry amali bor.
- Stream timeoutdan keyin orphan jarayon qolmaydi.
- Dashboard restartdan keyin xizmat active va API sog‘lom.

## Lokal buyruqlar

Python uchun doim:

```text
C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe -X utf8
```

Maqsadli testni ishga tushirish, so‘ng tegishli kengroq test fayllarini bajarish. HTML inline JavaScript’ni vaqtinchalik faylga ajratib `node --check` bilan tekshirish. Vaqtinchalik faylni tekshiruvdan keyin o‘chirish.

## Deploy ehtiyot choralari

- `.env`, `profiles.json`, `tokens.json` mazmunini o‘qib chiqarmaslik.
- Tokenli URL’ni browser navigation yoki terminalga yozmaslik.
- Remote shell commandda quotingni buzmaslik.
- Backup olmasdan production faylini almashtirmaslik.
- Static HTML va Python deployini ajratish; Python o‘zgarsa restart va latent 502’ni tekshirish.
- `profiles.json` va `/opt/bm/bm_automation` egasi `bm:bm` bo‘lib qolishini tekshirish.
