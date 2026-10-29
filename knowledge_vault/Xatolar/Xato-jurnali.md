# Xato jurnali

> Loyiha tarixida **tasdiqlangan** xatolar, ularning ildiz sabablari va
> qaytmaslik qoidalari. Biror muammo ko'rinsa — avval shu yerdan qidiring,
> keyin kodga qarang. Har bir qator alohida qaydga olib boradi.

## Jadval

| # | Xato | Belgi | Qayd | Holat |
|---|------|-------|------|-------|
| 1 | Reys/km tengsizligi (reja ≠ amalda + qabul qilinmagan) | raqamlar mos kelmaydi | [[km-hisoblash-xatolari]] | Tuzatilgan |
| 2 | `await` `async` funksiyasiz — dashboard bo'sh | konsolda `SyntaxError` | [[async-syntax-bug]] | Tuzatilgan |
| 3 | `.gitignore` `reports/` modulni yutib yubordi | `ModuleNotFoundError` | [[gitignore-reports-bug]] | Tuzatilgan |
| 4 | `profiles.name` unikal — demo seed `ON CONFLICT` | seed to'xtaydi | [[profiles-ruxsat]] | Tuzatilgan (migratsiya, 2026-10-26) |
| 5 | Ikki bot polling — `409 Conflict` | bot javob bermaydi | [[telegram-conflict]] | Qoida qo'shilgan |
| 6 | To'liq bo'lmagan deploy — dashboard `502` | nginx 502, traceback | [[vds-502-import]] | Tuzatilgan |
| 7 | Oylik Excel retry'larda takror yuborilgan | guruhga 2+ Excel | [[oylik-takror]] | Tuzatilgan |
| 8 | Vault prefiksi qo'shni papkani o'tkazgan | `../knowledge_vault2/...` ochilgan | [[path-traversal-vault]] | Tuzatilgan |
| 9 | AND ECO profil tokeni `401` — korxona tashlanib ketgan | daily'da korxona yo'q | [[and-eco-token]] | Tuzatilgan |

## Ildiz sabab bo'yicha indeks

| Sabab turi | Hodisalar |
|---|---|
| Sxema/muhit farqi (lokal ≠ VDS) | [[profiles-ruxsat]], [[vds-502-import]] |
| Noto'g'ri deploy tartibi | [[vds-502-import]] |
| Kod sintaksisi / frontend | [[async-syntax-bug]] |
| Git konfiguratsiyasi | [[gitignore-reports-bug]] |
| Hisoblash mantiqi | [[km-hisoblash-xatolari]] |
| Idempotentlik yo'qligi | [[oylik-takror]], [[telegram-conflict]] |
| Xavfsizlik chegarasi | [[path-traversal-vault]] |
| Auth/fallback | [[and-eco-token]] |

## Yangi xato qanday qo'shiladi

1. **Tasdiqlang** — taxminni jurnalga yozmang. Traceback, log yoki
   takrorlanadigan qadam bo'lsin.
2. Alohida qayd oching: `Xatolar/<qisqa-nom>.md`.
3. Tuzilma: **Semptom → Ildiz sabab → Dalil → Yechim → Qoida**.
4. Shu jadvalga qator qo'shing va indeksni yangilang.
5. Qayd nomi boshqa qayd bilan takrorlanmasin ([[Bilim-bazasi]] 3-qoida).
6. Hech qanday sir (token, parol, `.env` qiymati) qaydga yozilmaydi —
   faqat o'zgaruvchi **nomi** ([[Xavfsizlik]]).

## Umumiy saboqlar

1. **Lokal test — production kafolati emas.** Sxema, muhit va fayl
   to'plami farq qilishi mumkin.
2. **Deploy — bog'liqlik zanjiri.** Bitta fayl emas, u import qiladigan
   hammasi birga ketadi.
3. **Har qanday takrorlanadigan yuborish idempotent bo'lsin.**
4. **Chegarani satr bilan emas, haqiqiy yo'l bilan tekshiring.**
5. **Bitta korxona xatosi butun oqimni to'xtatmasin**, lekin xato
   yashirilmasin.
6. **Diagnostika tartibi:** log → traceback → takrorlash → tuzatish.
   Taxmin bilan fayl o'zgartirmang ([[Tekshirish-qoidalari]]).

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[profiles-ruxsat]]
- [[Bilim-bazasini-yuritish]]
- [[vds-502-import]]
- [[Asosiy-invariantlar]]

<!-- avto-bog'liq:end -->
