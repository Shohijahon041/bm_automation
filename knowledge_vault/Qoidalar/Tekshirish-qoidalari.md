# Tekshirish qoidalari

> Barcha agentlar uchun yagona "inson kabi tekshirish" preambulasi.
> Asl matn: `bm_automation/app/myai/prompts/shared.py` — `SHARED_PREAMBLE`.

## 1. Ma'lumot bilan ishlash

1. Taxmin qilmang — bilmang. Ma'lumot yetarli bo'lmasa, "Yetarli
   ma'lumot yo'q" deb ayting.
2. Arifmetikani o'zingiz "hisoblamang" — tool/kod natijasini ishlating.
3. Har bir "jami" raqamni mustaqil manba bilan solishtiring.
4. Invariantga zid natija — vault'dan qidiring ([[Asosiy-invariantlar]]).
5. QATNOV va AVTOBUS — alohida sonlar.
6. Xatoni tasdiqlasangiz — vault'ga yozing.
7. Ishonchingiz past bo'lsa — belgilang.

## 2. Kod o'zgarishidan keyin

| Tekshiruv | Buyruq |
|---|---|
| Python sintaksisi | `python -X utf8 -m py_compile <fayl>` |
| Python testlari | repo ildizidan `python -X utf8 -m pytest tests/ -q` |
| Aniq test fayli | `python -X utf8 -m pytest tests/test_knowledge_vault.py -q` |
| Inline JS | `<script>` blokini ajratib `node --check` |
| Element ID / funksiya nomi | `index.html`dagi chaqiruvchilar bilan solishtirish |

> Lokal Windows'da `python`/`py` ishlamaydi — to'liq interpreter yo'li
> ishlatiladi ([[Kod-va-uslub]]).

## 3. Deploydan oldin

1. O'zgargan fayllar ro'yxati va `md5` olinadi.
2. Serverdagi nusxa bilan farq tekshiriladi — faqat kerakli farq bo'lsin.
3. Serverda timestamp'li zaxira yaratiladi.
4. `.env`, `state/`, `profiles.json`, `tokens.json` **almashtirilmaydi**.
5. `py_compile` server venv'i bilan bajariladi.
6. Fayl egasi `bm:bm`, ruxsat `644` (papkalar `755`) bo'lishi tekshiriladi.

Batafsil: [[Ishga-tushirish-va-Deploy]].

## 4. Deploydan keyin

1. `systemctl is-active bm-bot bm-dashboard` — ikkalasi `active`.
2. `NRestarts=0` — crash-loop yo'q.
3. `journalctl -u ... --since "5 min ago"` — `Traceback`/`Exception` yo'q.
4. Yangi API yo'li anonim holatda kutilgan kod qaytaradi (odatda `401`).
5. Sahifa HTML'ida yangi bo'lim nomi bor.
6. Imkon bo'lsa — super-admin bilan haqiqiy o'qish/yozish/o'chirish sinaladi.
7. `md5` lokal bilan mos.

> "Test o'tdi" ≠ "funksiya ishlaydi". UI bo'lsa brauzerda ochib ko'rish;
> ochib bo'lmasa, buni **aniq aytish**.

## 5. Xatoni qayd etish

Har bir tasdiqlangan xato uchun:

```
Semptom  — foydalanuvchi nimani ko'radi
Sabab    — kod/sozlama darajasidagi ildiz
Dalil    — log, test, buyruq natijasi
Tuzatish — nima o'zgardi
Qoida    — qaytib qolmasligi uchun nima tekshiriladi
```

Yangi qayd ochib, [[Xato-jurnali]] jadvaliga qo'shiladi.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Asosiy-invariantlar]]
- [[Test-va-tekshiruv]]
- [[Kod-va-uslub]]
- [[Ishga-tushirish-va-Deploy]]

<!-- avto-bog'liq:end -->
