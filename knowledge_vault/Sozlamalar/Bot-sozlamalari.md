# Bot sozlamalari

> Telegram Operations Bot: buyruqlar, rollar, bo'lim ruxsatlari va
> `bot_settings` kalitlari. Bu qayd — botning "shpargalkasi".
> Ruxsat matritsasi to'liq: [[Ruxsatlar-va-rollar]]. Yuborish qoidalari:
> [[SMS-va-bildirishnoma-qoidalari]].

## 1. Buyruqlar (shaxsiy chat)

### Ko'rish
| Buyruq | Nima qaytaradi | Rol |
|---|---|---|
| `/today` | bugungi statistika | hamma |
| `/month` | oy boshidan bugungacha | hamma |
| `/routes` | yo'nalishlar | hamma |
| `/vehicles` | avtobuslar ro'yxati | hamma |
| `/vehicle <davlat raqami>` | bitta avtobus kartasi | hamma |
| `/drivers` | haydovchilar | hamma |
| `/driver <ism>` | haydovchi kartasi | hamma |
| `/trips [sana] [holat]` | reyslar (masalan `2026-08-10 REJECTED`) | hamma |
| `/problems` | muammolar (batafsil) | hamma |
| `/reports` | hisobotlar | hamma |
| `/errors` | xatolar | hamma |
| `/status` | tizim holati | hamma |

### Qo'shimcha hisobotlar
| Buyruq | Izoh |
|---|---|
| `/ai <savol>` | AI-yordamchi, tabiiy tilda |
| `/top km\|trips\|net\|attendance` | haydovchilar reytingi — **MANAGER va yuqori** |
| `/distance` | avtobuslar masofasi (km) |
| `/schedule <sana>` | yo'nalish jadvali |
| `/attendance <sana>` | davomat |
| `/alerts` | muammo alertlari holati |
| `/finesreport` | oylik jarima hisoboti (qo'lda) |
| `/insights` | AI o'z-o'zini tahlili (loglar + tavsiyalar) |

### Amallar
| Buyruq | Izoh | Rol |
|---|---|---|
| `/export xlsx\|csv\|pdf [scope]` | eksport | ADMIN, DISPATCHER |
| `/plan` | ertangi reja (haydovchi/grafik qo'shish) | ADMIN, DISPATCHER |
| `/brutto` | brutto-shartnoma to'lovi (116-son qaror) | ADMIN, MANAGER (`salary`) |
| `/hisob` | haydovchi oylik hisobi | ADMIN, MANAGER (`salary`) |
| `/sync` | kunlik sinxronlash | ADMIN, DISPATCHER |
| `/syncmonthly` | oylik sinxronlash | ADMIN, DISPATCHER |
| `/addcompany` | yangi firma ro'yxatdan o'tkazish | ADMIN |
| `/login` | firma egasi login/parolini kiritadi | hamma |
| `/resend` | profillarni qayta yuborish | ADMIN, DISPATCHER |

### Boshqa
| Buyruq | Izoh |
|---|---|
| `/start` | asosiy menyu |
| `/list` | so'nggi fayllar |
| `/vault` | bilim bazasi (super admin) — pastda |
| `/help` | yordam |
| `/myrole` | mening rolim |
| `/users` | bot foydalanuvchilari (ADMIN, DISPATCHER) |
| `/setrole <id> <rol>` | rol tayinlash (ADMIN) |
| `/sohbat` | MyAI bilan uzluksiz suhbat (qayta bosish — o'chirish) |

Excel (`.xlsx`) yuborilsa — sayt export formatida jadval to'ldiriladi.

## 2. Haydovchi rejimi

Haydovchi chat'i DB orqali (`find_driver_by_telegram`) aniqlanadi va unga
faqat o'z kartasi ko'rsatiladi:

| Buyruq | Izoh |
|---|---|
| `/today` | o'z kartasi (ish kunlari, qatnov, km, qatnashish) |
| `/settings` | profil va til |
| `/murojaat` | yangi murojaat/taklif |
| `/murojaatlarim` | murojaatlar holati |
| `/start`, `/myrole`, `/help` | umumiy |

Kompaniya, reyslar, muammolar bo'limlari haydovchiga **yopiq**. Qora
ro'yxatdagi (`blacklisted`) haydovchi DRIVER rolini olmaydi.

## 3. Guruh rejimi

Guruhda bot faqat quyidagilarga javob beradi: slash-buyruq, `@bot` eslatmasi,
botga reply. Javob berish huquqi — ADMIN/DISPATCHER/MANAGER. Javoblar qisqa
matn (to'liq karta/Excel/PNG emas).

| Buyruq | Izoh |
|---|---|
| `/today` | qisqa statistika |
| `/chiqish_on` | shu guruhda chiqish/obed eslatmasini yoqish |
| `/chiqish_off` | o'chirish |
| `/chiqish` | holatni ko'rish |

Guruh xabarlari `state/group_stats.json`ga yoziladi, `bot_users.json`ga emas.
Chiqish eslatmasi `AI_AUTO_AGENTS`'ga **bog'liq emas** — o'z holati bilan
ishlaydi ([[SMS-va-bildirishnoma-qoidalari]]).

## 4. Rollar va kirish modeli

| Rol | Daraja | Asosiy huquq |
|---|---|---|
| ADMIN | 4 | hamma narsa, `/addcompany`, `/setrole` |
| DISPATCHER | 3 | ko'rish + eksport/sinxron/plan/resend |
| MANAGER | 2 | o'z firmasi ko'rinishi + `salary` amallari |
| DRIVER | 1 | faqat o'z kartasi va murojaatlar |
| VIEWER | 0 | faqat ko'rish |

Rol aniqlash ustunligi:
1. `TG_ADMIN_IDS` / `TG_DISPATCHER_IDS` / `TG_MANAGER_IDS` / `TG_DRIVER_IDS` (env);
2. runtime'da `/setrole` bilan berilgan rol (`state/bot_users.json`);
3. DB haydovchi bog'lanishi → DRIVER;
4. `TG_DEFAULT_ROLE` (standart `viewer`).

Kirish:
- **Ochiq rejim** — `TG_ALLOWED_IDS` yo'q va `TG_STRICT_ACCESS` o'chiq: hamma
  `/start` bosishi mumkin, amallar rol bilan cheklanadi.
- **Qat'iy rejim** — `TG_ALLOWED_IDS` berilgan yoki `TG_STRICT_ACCESS=on`:
  ro'yxatda yo'q chat butunlay bloklanadi.

Ruxsat yo'q javobi: `⛔ Sizda bu amal uchun huquq yo'q.`

Super admin (bilim bazasi va ba'zi maxfiy amallar): `TG_OWNER_IDS` yoki
dashboard'dagi super foydalanuvchining `telegram_chat_id`si.

## 5. Bo'lim ruxsatlari (per-user)

Admin panelda foydalanuvchiga **qaysi bo'limlar** ochiq bo'lishini belgilaydi.
Bir xil ro'yxat ikkala tomonda ishlaydi:

- panel menyusi va sahifa API'si (`403` bilan to'siladi);
- bot buyruqlari va nav callback'lari.

Kalitlar `bm_automation/app/core/sections.py::SECTION_KEYS` da (panel
`PAGE_ROLES` bilan **bir xil bo'lishi shart**). Bot buyrug'i → bo'lim
xaritasi `BOT_SECTIONS` da:

| Bo'lim | Bot buyruqlari |
|---|---|
| overview | today, month, status, dashboard |
| routes / vehicles / drivers | routes, vehicles, driver, drivers |
| top-performance | top, leaderboard |
| performance | distance, attendance, reports, export |
| dispatch | trips, schedule, plan, sync, syncmonthly, resend, grafik |
| alerts | alerts, problems |
| appeals | murojaat, murojaatlarim |
| insights / myai | ai, sohbat, myai |
| settings / users / dusers | settings, addcompany, users, setrole |
| documents | list, doc, docs |
| knowledge | vault |

Cheklovsiz (`pages = None`) — faqat rol ishlaydi. Ro'yxat berilganda rol
yuqori chegara bo'lib qoladi: rol yetmasa ro'yxatdagi bo'lim ham ochilmaydi.
Noma'lum kalit jimgina tashlanmaydi — xato qaytariladi.

## 6. `bot_settings` kalitlari

`state/bot_settings.json` — panelda o'zgartiriladigan, audit yoziladigan
sozlamalar (`state/bot_settings_audit.json`):

| Kalit | Ma'nosi | Chegara / default |
|---|---|---|
| `elec_price` | elektr narxi | ≥ 0 |
| `brutto_skm` | brutto shartnoma SKM | ≥ 0 |
| `route_km[route_id]` | yo'nalish km | ≥ 0 |
| `route_skm[route_id]` | yo'nalish SKM | ≥ 0 |
| `route_tariff[route_id]` | tarif (QQS bilan/bez) | ≥ 0 |
| `bot_driver_salary` | haydovchi oyligini botda ko'rsatish | `false` (default) |
| `auto_sync` | avtomatik sinxronlash | `false` |
| `auto_sync_interval_min` | sync oralig'i (daqiqa) | 5..1440 |
| `departure_offset_min` | chiqish eslatmasini surish | -180..+180 daq |
| `langs[chat_id]` | foydalanuvchi tili | `uz` |

Eslatma: eski `show_driver_salary` kaliti **e'tiborsiz** — faqat
`bot_driver_salary` o'qiladi. Qiymatlarni qo'lda JSON'da tahrirlashdan
saqlaning: panel audit yozadi, qo'lda o'zgartirish yozmaydi.

## 7. Avtomatik agentlar (qachon ishlaydi)

`poll_forever()` ichidagi agentlar faqat `AI_AUTO_AGENTS=on` bo'lganda
ishlaydi (default `off` — bot faqat buyruqqa javob beradi):

| Agent | Qo'shimcha kalit |
|---|---|
| Kunlik xulosa | `AI_DAILY_SUMMARY`, `AI_DAILY_SUMMARY_HOUR` |
| Self-review | `AI_SELFREVIEW`, `AI_SELFREVIEW_HOUR` |
| Muammoli reyslar / hujjat muddati / oylik natijalar | `AI_AUTO_AGENTS` |
| Jarimalar | `FINES_REPORT` |

Mustasno: `group_departures.check_and_send()` har poll'da chaqiriladi, lekin
`GROUP_DEPARTURE_NOTIFY` va guruh holatiga qarab ([[Ish-oqimlari]]).
Avtomatik sync (`auto_sync`) ham o'z holatiga qarab ishlaydi.

## 8. `/vault` — bilim bazasi

Super admin uchun ([[Ruxsatlar-va-rollar]]):

- `/vault` — ro'yxat;
- `/vault <path>` — qaydni o'qish (`[[Qayd]]` basename ham ishlaydi);
- `/vault qidir <so'z>` — qidirish;
- `/vault yoz Kat|Sarlavha|Matn` — yozish.

Bir xil vault'ni panel (`#knowledge`) va MyAI (`myai/tools/vault.py`) ham
ko'radi — yagona manba `knowledge_vault/` ([[Muhit-va-konfiguratsiya]]).

## 9. Ishga tushirish / tekshirish

- Bot: `python.exe -X utf8 -u -m bm_automation bot --watchdog`
  (`--once` — bitta poll, smoke test uchun).
- **Bitta poller qoidasi**: ikki nusxa ishga tushsa Telegram `409 Conflict`
  beradi ([[telegram-conflict]]). Restartdan oldin eski jarayon yopilganini
  tekshiring.
- `.env` va kod o'zgarsa botni **restart** qilish shart — `load_dotenv()`
  import paytida bir marta o'qiladi.
- Tekshiruv: `/myrole`, `/status`, `/help`; loglar
  `state/bot_stdout.log`, `state/bot_stderr.log`.

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Ruxsatlar-va-rollar]]
- [[Ish-oqimlari]]
- [[Arxitektura]]
- [[Bot-buyruqlari]]

<!-- avto-bog'liq:end -->
