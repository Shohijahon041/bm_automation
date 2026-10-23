# BM Dashboard — maxfiylik siyosati

**Hujjat versiyasi:** 1.0  
**Kuchga kirish sanasi:** [___-__-____]  
**Ma'lumotlar operatori:** [Tashkilotning to'liq nomi]  
**STIR:** [__________]  
**Manzil:** [__________]  
**Telefon:** [__________]  
**Email:** [__________]

> Bu hujjat namuna sifatida tayyorlandi. Uni e'lon qilishdan va shaxsiy
> ma'lumotlarni amalda qayta ishlashdan oldin yuridik ekspertiza qiling.
> `[___]` bilan belgilangan maydonlarni tashkilot rekvizitlari bilan to'ldiring.

## 1. Umumiy qoidalar

1.1. Ushbu Siyosat BM Dashboard (keyingi o'rinlarda — **Tizim**) doirasida
shaxsiy ma'lumotlarni qanday yig'ish, ishlatish, saqlash, himoya qilish va
o'chirishni tushuntiradi.

1.2. Siyosat O'zbekiston Respublikasining "Shaxsiy ma'lumotlar to'g'risida"gi
qonuni va boshqa tegishli normativ hujjatlarga muvofiq tayyorlangan.

1.3. Tizimdan foydalanuvchi shaxs Siyosat bilan tanishgan va unda ko'rsatilgan
qayta ishlash shartlariga rozilik bildirgan hisoblanadi. Rozilik ixtiyoriy;
biroq ayrim ma'lumotlarsiz Tizimning tegishli funksiyalari ishlamasligi mumkin.

1.4. Tizim voyaga yetmagan shaxslarga mo'ljallanmagan.

## 2. Qanday ma'lumotlar qayta ishlanadi

### 2.1. Foydalanuvchi (akkaunt) ma'lumotlari

- login, parol hash holatida (ochiq parol saqlanmaydi);
- rol, kompaniya, bo'limlar bo'yicha ruxsatlar;
- Telegram chat ID va foydalanuvchi nomi (agar bog'langan bo'lsa);
- sessiya yozuvlari, kirish/chiqish vaqti, IP-manzil, brauzer ma'lumotlari;
- audit yozuvlari (kim, qachon, qaysi amalni bajargani).

### 2.2. Haydovchi va transport ma'lumotlari

- F.I.SH., telefon, JSHSHR/STIR, jins, tug'ilgan sana (agar kiritilgan bo'lsa);
- pasport seriyasi, raqami, berilgan joyi va muddati;
- haydovchilik guvohnomasi raqami, toifasi va muddati;
- pasport/guvohnoma rasmlari va haydovchi fotosi;
- ish kunlari, km qaydlari, qatnovlar, reys vaqti, avtobus biriktirilishi;
- jarimalar, murojaatlar va ular bo'yicha holat;
- ish haqi, brutto, soliq va boshqa hisob-kitob ko'rsatkichlari;
- GPS/telemetriya ma'lumotlari (agar tegishli modul ulangan bo'lsa).

### 2.3. Texnik ma'lumotlar

- IP-manzil, qurilma va brauzer turi, sahifa ochilish vaqti;
- xatoliklar jurnallari, so'rovlar tarixi;
- cookie va sessiya identifikatorlari.

## 3. Qayta ishlash maqsadlari

Ma'lumotlar quyidagi maqsadlarda ishlatiladi:

- foydalanuvchini autentifikatsiya qilish va sessiyani boshqarish;
- kompaniya bo'yicha haydovchilar, reyslar va hisob-kitoblarni yuritish;
- ish haqi, jarima va boshqa hisobotlarni shakllantirish;
- BM sayti bilan ma'lumotlarni sinxronlash;
- Telegram orqali eslatma va hisobot yuborish;
- tizim xavfsizligini ta'minlash, firibgarlik va ruxsatsiz kirishni aniqlash;
- foydalanuvchi so'rovlariga javob berish va texnik yordam ko'rsatish;
- qonun hujjatlarida nazarda tutilgan majburiyatlarni bajarish.

## 4. Qayta ishlashning huquqiy asoslari

- foydalanuvchi bilan tuzilgan shartnoma yoki shartnoma tuzishdan oldingi harakatlar;
- foydalanuvchining aniq roziligi;
- operatorning qonuniy manfaatlari (xavfsizlik, firibgarlikning oldini olish);
- qonun hujjatlarida belgilangan majburiyatlar;
- haydovchi ma'lumotlari bo'yicha — tashkilotning mehnat va hisob-kitob
  majburiyatlari, tegishli rozilik yoki qonuniy asos.

## 5. Cookie va sessiyalar

5.1. Tizim autentifikatsiya uchun `bm_session` nomli `httpOnly` cookie'dan
foydalanadi. U brauzer JavaScript'i orqali o'qilmaydi va odatda **12 soat**
amal qiladi.

5.2. Cookie reklama maqsadida ishlatilmaydi. Tizim faqat ishlashi uchun zarur
bo'lgan cookie'lardan foydalanadi.

5.3. Brauzerda cookie'larni o'chirish mumkin, biroq bu holda Tizimga kirish
ishlamaydi.

## 6. Ma'lumotlarni uchinchi shaxslarga berish

Ma'lumotlar quyidagi hollarda berilishi mumkin:

- **hosting va bulut provayderi** — Tizimni joylashtirish va zaxira nusxalash;
- **Telegram** — foydalanuvchi roziligi bilan eslatma/hisobot yuborilganda;
- **BM (bm.dtransport.uz) API** — ma'lumotlarni sinxronlash uchun;
- **audit, buxgalteriya yoki yuridik xizmat ko'rsatuvchi tashkilotlar** —
  qonuniy majburiyatlar doirasida;
- **davlat organlari** — faqat qonunda nazarda tutilgan tartibda va asosda.

Ma'lumotlar reklama maqsadida sotilmaydi yoki ijaraga berilmaydi.

## 7. Saqlash, joylashuv va himoya

7.1. Ma'lumotlar [server joylashuvi: O'zbekiston / boshqa davlat] hududidagi
serverlarda saqlanadi. Chet davlatga uzatish yuz berganda, amaldagi qonunchilik
talablari va tegishli himoya choralari qo'llaniladi.

7.2. Himoya choralari:

- HTTPS/TLS orqali shifrlangan ulanish;
- parollarni qaytarib bo'lmaydigan hash ko'rinishida saqlash;
- rol va kompaniya bo'yicha server darajasidagi cheklovlar;
- sessiyalarni boshqarish, urinishlarni cheklash va audit jurnallari;
- muntazam zaxira nusxalar va kirish huquqlarini cheklash.

7.3. Hech bir tizim mutlaq xavfsizlikni kafolatlamaydi. Ma'lumotlar buzilishi
aniqlansa, operator amaldagi qonunchilikda belgilangan tartibda choralar ko'radi
va zarur xabarnomalarni yuboradi.

## 8. Saqlash muddati

- akkaunt ma'lumotlari — akkaunt faol bo'lganda va u yopilgandan keyin
  qonunchilikda talab qilingan muddat davomida;
- audit va xavfsizlik jurnallari — odatda 12 oy;
- haydovchi va ish haqi ma'lumotlari — tashkilotning buxgalteriya va mehnat
  hujjatlari uchun belgilangan muddatlarda;
- zaxira nusxalar — texnik tsikl bo'yicha, belgilangan muddatdan oshmaydi.

Muddat tugagach, ma'lumotlar o'chiriladi yoki shaxsni aniqlab bo'lmaydigan
holatga keltiriladi.

## 9. Foydalanuvchining huquqlari

Foydalanuvchi quyidagi huquqlarga ega:

- o'z ma'lumotlari qayta ishlanayotgani haqida ma'lumot olish;
- ma'lumotlarni ko'rish va nusxasini olish;
- noto'g'ri yoki eskirgan ma'lumotlarni tuzatish;
- rozilikni qaytarib olish va ma'lumotlarni o'chirishni talab qilish
  (qonuniy saqlash majburiyatlari bundan mustasno);
- qayta ishlashga e'tiroz bildirish;
- nazorat organiga shikoyat qilish.

So'rovni yuborish uchun: `[email]`. So'rov shaxsni tasdiqlovchi ma'lumotlar
bilan birga yuborilishi kerak. Javob qonunchilikda belgilangan muddatda beriladi.

## 10. Bolalar

Tizim voyaga yetmagan shaxslarning ma'lumotlarini ataylab yig'maydi. Agar
bunday ma'lumot tasodifan kiritilgan bo'lsa, operatorga xabar bering; u
ma'lumotni o'chiradi.

## 11. Siyosatga o'zgartirishlar

Siyosat yangilanishi mumkin. Yangi versiya Tizimda e'lon qilinadi va e'lon
qilingan sanadan kuchga kiradi. Muhim o'zgarishlar haqida foydalanuvchilarga
qo'shimcha xabar berilishi mumkin.

## 12. Bog'lanish

Savollar, so'rovlar va shikoyatlar uchun:

- **Tashkilot:** [Tashkilotning to'liq nomi]
- **Manzil:** [__________]
- **Telefon:** [__________]
- **Email:** [__________]
- **Mas'ul shaxs:** [F.I.SH., lavozim]
