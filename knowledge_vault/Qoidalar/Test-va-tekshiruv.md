# Test va tekshiruv

> Kod o'zgargach: avval shu o'zgarishga tegishli testlarni ishga tushiring,
> keyin to'liq to'plamni. Faqat "compile bo'ldi" bilan to'xtamang.

## Buyruqlar

```powershell
& <python> -X utf8 -m pytest tests/ -q                 # to'liq to'plam (~3.5 daq)
& <python> -X utf8 -m pytest tests/test_myai.py -q -k planner
& <python> -X utf8 -m py_compile bm_automation/app/core/knowledge_grow.py
```

CI (`.github/workflows/test.yml`) — Python **3.12**,
`BM_DB_DRIVER=sqlite`, `BM_TEST_MODE=1`. Lokal interpreter 3.14 bo'lishi
mumkin; shuning uchun kod ikkalasida ham ishlashi kerak.

## Izolyatsiya (conftest)

- `tests/conftest.py` bot sozlamalarini neytrallashtiradi (`km_rate = 0.0`)
  va `KNOWLEDGE_VAULT_DIR` ni vaqtinchalik papkaga o'giradi — testlar
  haqiqiy vault'ga yozmaydi.
- `tests/sqlite_backend.py` — `Storage` bilan mos SQLite nusxasi; sxema
  `app/db/schema.py` dan avtomatik quriladi. `storage` fixture orqali
  olinadi (`storage_for(SQLiteDatabase(...))`).
- Jonli `state/*.json` testlarga ta'sir qilmasligi kerak.

## Qoidalar

- Modul ichidagi nomni patch qilsangiz — **ishlatiladigan modulda** patch
  qiling: `monkeypatch.setattr(daily_summary, "get_storage", ...)`.
- Rejalashtirishni tekshirganda modul interval hisoblagichlarini
  (masalan `_LAST_CHECK_AT`) test boshida reset qiling.
- Yangi endpoint/qayd qo'shsangiz, unga test yozing — ayniqsa ruxsat
  (403), validatsiya va chegara holatlari uchun.
- Ishlayotgan kodni "testni bo'shatish" bilan yashirmang: assertionni
  zaiflashtirish, xatoni yutish yoki testni o'chirish mumkin emas. Test
  noto'g'ri yo'lni tekshirayotgan bo'lsa (masalan deterministik template
  LLM'ni chetlab o'tsa) — testni to'g'ri yo'lga qaratib tuzatish kerak,
  taxminni emas.
- `web/index.html` ichidagi inline JS o'zgarsa — `<script>` blokini
  ajratib `node --check` bilan sintaksisini tekshiring; mavjud element
  `id` va funksiya nomlarini sababsiz o'zgartirmang.
- Xato chiqsa — sababini tuzating; testni o'chirib yoki belgini
  yashirib "o'tkazib" yubormang.

## Nima tekshiriladi

| Modul | Test fayli |
| --- | --- |
| Bilim bazasi | `tests/test_knowledge_vault.py`, `tests/test_vault_links.py` |
| Agentlar | `tests/test_myai.py`, `tests/test_myai_knowledge.py` |
| Dashboard | `tests/test_dashboard.py`, `tests/test_fastapi_dashboard.py` |
| Bot | `tests/test_ops_bot.py`, `tests/test_driver_schedule_bot.py` |
| GPS | `tests/test_jt808.py`, `tests/test_jt1078.py`, `tests/test_gps_*.py` |

Bog'liq: [[Qoidalar/Kod-va-uslub]] · [[Qoidalar/Bilim-bazasini-yuritish]]

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Testlar]]
- [[Kod-va-uslub]]
- [[Tekshirish-qoidalari]]
- [[Bilim-bazasini-yuritish]]

<!-- avto-bog'liq:end -->
