# Xavfsizlik

> Qoida: **maxfiy qiymat hech qachon** chatga, logga, commitga yoki hujjatga
> yozilmaydi. Faqat o'zgaruvchi **nomi** aytiladi.

## 1. Sir saqlash

| Nima | Qayerda | Mumkin emas |
|---|---|---|
| BM login/token | `.env`, `bm_automation/tokens.json` | chat, log, commit, skrinshot |
| Telegram bot token | `.env` | kodga hardcode |
| `DASHBOARD_TOKEN` | `.env` | URL'ga chiqarish, exception repr |
| `VIDEO_TOKEN_SECRET` | `.env` | log, URL |
| DB paroli | `.env` | hujjat, `DEPLOY_VDS.md` |
| GPS API token | `.env` / sessiya | navigate URL, `evaluate_script` natijasi |
| `profiles.json` | serverda, `0600` | `cat`/`diff` bilan to'liq ko'rsatish |

- Xato matnida `Authorization` header yoki tokenli URL chiqmasligi
  tekshiriladi.
- `.env`dan olingan tokenlardan `\r\n` tozalanadi.
- Repo **public** — har commitdan oldin sir skani.

## 2. Sessiya va autentifikatsiya

- Dashboard: `dashboard_users` (pbkdf2-sha256), httpOnly `bm_session`
  cookie (12 soat), `state/dashboard_sessions.json`.
- Bearer sessiya tokeni cookie'dan ustun; `DASHBOARD_TOKEN` bearer sifatida
  tenant tekshiruvidan chetlab o'tish uchun ishlatilmaydi.
- `DASHBOARD_TOKEN` bo'sh bo'lmasligi shart — aks holda API himoyasiz
  qolishi mumkin.
- Login urinishlari cheklangan; ko'p xato → vaqtincha blok.
- Sessiya bekor qilinsa (logout, blok) — keyingi so'rov `401`.

## 3. Rate-limit va audit

- POST `/api/*` — endpoint bo'yicha 1 soniyada 1 so'rov; oshsa `429`.
- Login/parol urinishlari alohida hisoblanadi.
- Mutatsiyalar audit logga yoziladi (parol/token yozilmaydi).
- Grafik yuborish — korxona bo'yicha kuniga 20 ta.

## 4. Tenant va ruxsat

- Har bir API'da rol + kompaniya tekshiriladi; bloklangan tenant → `403`.
- Fail-closed: sessiya/kompaniya aniqlanmasa — ruxsat yo'q.
- UI'da yashirish — himoya emas; serverda ham tekshiriladi
  ([[Ruxsatlar-va-rollar]]).

## 5. Fayl yo'llari

- Bilim bazasida `Path.resolve()` + ota-papkalar zanjiri tekshiriladi;
  `../` bilan vault tashqarisiga chiqib bo'lmaydi ([[path-traversal-vault]]).
- O'chirish faqat `.md` fayl bilan cheklangan.
- Yuklangan fayl nomlari serverda qayta tekshiriladi; shell buyrug'iga
  foydalanuvchi matni to'g'ridan-to'g'ri qo'shilmaydi.

## 6. Server (VDS)

- SSH faqat kalit bilan; parol login o'chirilgan.
- `fail2ban`: `[sshd]` va `[bm-login]` (dashboard login 8 xato → 1 soat ban).
- nginx: TLS 1.2+, HSTS, `server_tokens off`, xavfsizlik headerlari.
- Dashboard faqat `127.0.0.1:8080`da tinglaydi; tashqi kirish nginx orqali.
- `profiles.json` va uning papkasi `bm:bm` bo'lishi shart — aks holda
  tizim faqat bitta firma ko'rsatadi ([[profiles-ruxsat]]).

## 7. Bot

- Telegram bot faqat **bitta joyda** polling qiladi — VDS'da. Lokal botni
  parallel ochish conflict beradi ([[telegram-conflict]]).
- Guruhda bot faqat buyruq/mention/reply'ga javob beradi.
- Haydovchi shaxsiy chatda; guruh javobi faqat rahbariyatga.
- `/vault` faqat super-egasi uchun.

## 8. Video/GPS

- Kamera va GPS API'lari autentifikatsiya + kompaniya scope'ini serverda
  tekshiradi.
- Video token URL'ga qo'yilmaydi; HMAC tokenning muddati va scope'i
  tekshiriladi.
- Snapshot/stream sessiyasi tugagach resurslar yopiladi (ffmpeg/muxer
  osilib qolmasin).
- `CSP`ni muammoni yashirish uchun kengaytirish taqiqlanadi.

## Hodisa bo'lsa

1. Tokenni almashtirish (BM, bot, dashboard).
2. Loglarda qiymat chiqmaganini tekshirish va zarur joyda tozalash.
3. Sababni [[Xato-jurnali]]ga yozish (qiymatsiz).
4. Qayta sodir bo'lmasligi uchun qoida qo'shish.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Dashboard-auth-va-sessiya]]
- [[Arxitektura]]
- [[Integratsiyalar]]
- [[Xato-jurnali]]

<!-- avto-bog'liq:end -->
