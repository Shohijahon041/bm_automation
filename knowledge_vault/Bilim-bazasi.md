# Bilim bazasi

BM Automation loyihasining **yagona bilim markazi**. Bu yerdan boshlang: har bir
mavzu alohida qaydda, qaydlar bir-biriga `[[havola]]` bilan bog'langan.

> Qoida: bir qayd — bitta mavzu. Yangi bilim paydo bo'lsa yangi qayd ochiladi va
> shu indeksga qo'shiladi. Eski qayd ustiga "hammasi" yozilmaydi.

## Tez boshlash

| Savol | Qayd |
|---|---|
| Tizim qanday qismlardan iborat? | [[Arxitektura]] |
| Kunlik/oylik ish qanday ketadi? | [[Ish-oqimlari]] |
| Qaysi so'z nima degani? | [[Atamalar]] |
| Hisobda qanday xato bo'lmasin? | [[Asosiy-invariantlar]] |
| Nimani va qanday tekshirish kerak? | [[Tekshirish-qoidalari]] |
| Kim nimani ko'radi? | [[Ruxsatlar-va-rollar]] |
| Serverga qanday chiqariladi? | [[Ishga-tushirish-va-Deploy]] |
| Nima buzilgan edi, qanday tuzatildi? | [[Xato-jurnali]] |

## Tuzilma

### 1. Umumiy
- [[Arxitektura]] — qismlar, ma'lumot oqimi, texnologiyalar
- [[Ish-oqimlari]] — kunlik, oylik, jadval, haydovchi, token jarayonlari
- [[Atamalar]] — reys, qatnov, konechka, SKM, tenant, vault va h.k.

### 2. Qoidalar
- [[Asosiy-invariantlar]] — buzilmas hisob va xavfsizlik qoidalari
- [[Tekshirish-qoidalari]] — "inson kabi tekshirish" preambulasi va deploy tekshiruvi
- [[Kod-va-uslub]] — kod uslubi, testlar, JS tekshiruvi, git tartibi
- [[Ruxsatlar-va-rollar]] — dashboard/bot rollari, per-user bo'limlar, tenant scope
- [[Xavfsizlik]] — sir saqlash, sessiya, rate-limit, SSH, path-chegara
- [[Bilim-bazasini-yuritish]] — bilim bazasi qanday o'sadi (avtomatik kengaytirish)
- [[Kop-korxonali-izolyatsiya]] — `company_id` filtri, rol matritsasi, moliyaviy yashirish
- [[Dashboard-auth-va-sessiya]] — parol (pbkdf2), `bm_session` cookie, rate-limit
- [[Agentlar-arxitekturasi]] — myai pipeline, least privilege, o'rganish sikli
- [[Sinxronizatsiya-oqimi]] — BM → DB oqimi va fon agentlari jadvali
- [[GPS-kuzatuv]] — JT808/JT1078 portlari, modullar, geofence sozlamalari
- [[Test-va-tekshiruv]] — pytest, izolyatsiya, patch qoidasi, JS tekshiruvi

### 3. Sozlamalar
- [[Ishga-tushirish-va-Deploy]] — VDS runbook: zaxira, fayl ko'chirish, restart, rollback
- [[Muhit-va-konfiguratsiya]] — `.env` kalitlari (faqat nomlari), `state/` fayllari
- [[Integratsiyalar]] — BM/OneID, Telegram, GPS/JT808/JT1078, ZLMediaKit, PostgreSQL
- [[SMS-va-bildirishnoma-qoidalari]] — SMS, grafik limiti, guruh eslatmalari
- [[Bot-sozlamalari]] — buyruqlar, rollar, bo'lim ruxsatlari, `bot_settings` kalitlari

### 4. Xatolar
- [[Xato-jurnali]] — barcha tasdiqlangan hodisalar jadvali (kirish nuqtasi)
- [[km-hisoblash-xatolari]] — reys/km tengsizligi va km-narx ustuvorligi
- [[async-syntax-bug]] — `await` tushib qolgani sabab dashboard bo'sh qolgani
- [[gitignore-reports-bug]] — `reports/` qoidasi modulni yutib yuborgani
- [[profiles-ruxsat]] — `profiles.json` egaligi va VDSda `profiles.name` unikal cheklovi
- [[telegram-conflict]] — ikki bot birga polling qilib `getUpdates Conflict` bergani
- [[vds-502-import]] — to'liq bo'lmagan deploy sabab dashboard 502 bo'lgani
- [[oylik-takror]] — oylik Excel retry'larda bir necha marta yuborilgani
- [[path-traversal-vault]] — vault prefiks tekshiruvi qo'shni papkani o'tkazib yuborgani
- [[and-eco-token]] — profil tokeni 401 — asosiy tokenga fallback

### 5. Hisobotlar
`Hisobotlar/` papkasi **avtomatik** to'ldiriladi:
- `Kunlik-hisobot-YYYY-MM.md` — har kuni kunlik xulosa qo'shiladi;
- `Oylik-natijalar <oy>.md` — oylik natijalar yuborilganda saqlanadi;
- [[Kod-xaritasi]], [[API-endpointlar]], [[Bot-buyruqlari]], [[Testlar]],
  [[Muhit-sozlamalari]], [[DB-jadvallari]], [[Holat-snapshoti]] — kod va
  holat xaritalari (`knowledge_grow`);
- [[Agent-vazifalari]] — bajarilgan topshiriqlar tarixi;
- [[Grafik-holati]] — grafik salomatligi (tugun, havola, yetim, uzilgan).

[[Log-tahlili]] va [[Avto-xato-jurnali]] ham avtomatik qaydlardir
(log guruhlari va agent/tool xatolari).

Bu fayllar qo'lda tahrirlanmaydi — ular tarix yoki hisob-kitob natijasi
([[Bilim-bazasini-yuritish]]).

## Nega bu tizim ishlaydi

1. **Fayl — haqiqat manbasi.** Qaydlar PostgreSQL'da emas, oddiy `.md`
   fayllarda. Baza buzilsa ham bilim qoladi; `git` bilan tarixini ko'rish,
   Obsidian bilan ochish, istalgan muharrir bilan tahrirlash mumkin.
2. **Papka = kategoriya.** `Qoidalar/`, `Sozlamalar/`, `Xatolar/`,
   `Hisobotlar/` — panel, bot va LLM bir xil chegaralarni ko'radi
   (`bm_automation/app/core/knowledge.py`, `CATEGORIES`).
3. **Sarlavha = havola.** `[[Asosiy-invariantlar]]` yozilsa, tizim shu nomli
   yagona faylni topadi. Shu sababli **fayl nomlari takrorlanmasligi** kerak;
   ikki xil papkada bir xil nom bo'lsa, havola aniqlanmaydi.
4. **Uch xil kirish, bitta manba.** Dashboard "Bilim bazasi", bot `/vault` va
   LLM `myai/tools/vault.py` — hammasi shu papkani o'qiydi/yozadi. Bir joyda
   yozilgan qayd qolgan ikkalasida darhol ko'rinadi.
5. **Faqat egasi ko'radi.** Bilim bazasida ichki qoidalar va xato tafsilotlari
   bor; shuning uchun dashboardda ham, botda ham **super-admin** tekshiruvi
   majburiy ([[Ruxsatlar-va-rollar]], [[Xavfsizlik]]).
6. **Chegara himoyalangan.** Har bir yo'l `..` orqali vault tashqarisiga
   chiqishdan tekshiriladi; faqat `.md` fayllar o'chiriladi; bitta qayd
   400 KB dan oshmaydi ([[path-traversal-vault]]).
7. **Bilim o'z-o'zidan to'planadi.** Kunlik xulosa va oylik natijalar
   `Hisobotlar/` ga avtomatik yoziladi; bundan tashqari `knowledge_grow`
   kodni, holatni va loglarni skanerlab, avtomatik qaydlar qo'shadi va
   yechilmagan havolalar uchun bo'sh qayd ochadi ([[Bilim-bazasini-yuritish]]).
8. **Qidiruv oddiy va tez.** Qidiruv sarlavha + butun matn bo'yicha ishlaydi;
   minglab fayl emas, o'nlab qayd — natija darhol chiqadi.

## Qayd yozish tartibi

1. Mavzu aniq bo'lsin: "nima bu, nima uchun shunday, qanday tekshiriladi".
2. Sarlavha boshqa qayd bilan bir xil bo'lmasin.
3. Mazmunan muhim joyga `[[havola]]` qo'shing; qolgan bog'lanishni tizim
   o'zi qo'shadi (pastdagi bo'lim).
4. Xato bo'lsa: **semptom → ildiz sabab → dalil → tuzatish → qoida**.
5. Eski ma'lumot eskirsa — o'chirish emas, yangilash va sanani yozish.

## Avtomatik bog'lash

Har bir qayd oxiriga markerlar orasida **"Bog'liq qaydlar"** bloki
avtomatik yoziladi:

```
<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Xato-jurnali]] — mazmuni bo'yicha eng yaqin 4 ta qayd
<!-- avto-bog'liq:end -->
```

- Qayd yozilganda (`write_note`/`append_note`) blok **o'sha zahoti** qayta
  hisoblanadi; yangi qayd eski qaydlarning ro'yxatiga ham tushadi.
- O'xshashlik: sarlavha 4x vazn bilan hisoblangan token chastotasi
  (kod bloklari hisobga olinmaydi) → kosinus o'lchovi.
- Blokni qo'lda tahrirlash shart emas — keyingi yozuvda almashtiriladi.
- Butun vault'ni birdan yangilash: `python -m bm_automation vault-links`
  (`--dry-run` — faqat ko'rsatadi, `--limit N` — nechta havola).
- Idempotent: o'zgarish bo'lmasa fayl qayta yozilmaydi.

## Grafik ko'rinishi

- Obsidian'da `Ctrl+G` — grafik. Ranglar papka bo'yicha: Qoidalar — ko'k,
  Sozlamalar — binafsha, Xatolar — qizil, Hisobotlar — yashil, ildiz
  qaydlari — kulrang.
- **Jonli holat:** dashboard → Bilim bazasi → yuqoridagi kartalar va
  "GRAFIK" tugmasi: qaydlar, havolalar, yetim qaydlar, uzilgan havolalar
  hamda eng bog'langan qaydlar.
- Avtomatik hisobot: [[Grafik-holati]] — `knowledge_grow` har
  yangilanishda qayta yozadi (o'zini hisobdan chiqarib, idempotent).
- "Yetim" — hech qanday havolasi yo'q qayd; "uzilgan" — mavjud bo'lmagan
  qaydga qilingan `[[havola]]`. Ikkalasi ham 0 bo'lsa grafik sog'lom.
