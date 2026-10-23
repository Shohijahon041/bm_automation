# Code Review Prompt

## Maqsad
Loyihadagi kodni xavfsizlik, to'g'rilik va samaradorlik bo'yicha tekshirish.

## Kontekst
- Loyiha: BM Automation (transport operations bot)
- Asosiy fayllar: `bm_automation/app/notifications/ops/`, `bm_automation/app/dashboard/`, `bm_automation/app/core/`
- Testlar: `tests/` (pytest, SQLite izolatsiya)

## Tekshirish Ro'yxati

### Xavfsizlik
- [ ] `.env` / `tokens.json` / `profiles.json` gitga commitalinmaganmi?
- [ ] SQL injection / path traversal mumkinmi?
- [ ] CORS, CSRF, XSS himoyalari bor-mi?
- [ ] Rate limiting va brute-force himoyasi bor-mi?
- [ ] Secretlar (token, password) loglarda chiqmayaptimi?
- [ ] Role-based access control (RBAC) barcha endpointlarda ishlayaptimi?

### To'g'rilik
- [ ] `async`/`await` to'g'ri ishlatilganmi? (event loop bloklanmayaptimi?)
- [ ] Transaction'lar to'g'ri yopilmoqmi? (commit/rollback)
- [ ] Race condition'lar oldini olinganmi? (atomic_write, lock)
- [ ] Exception handling — xatolar yutilib qolinmayaptimi?
- [ ] Retry/backoff logika (BMClient, notify) ishlayaptimi?

### Samaradorlik
- [ ] N+1 query muammosi yo'qmi?
- [ ] Kengaytirilgan ma'lumotlar (loglar, reports) tozalanmoqmi?
- [ ] Caching / indekslar ishlatilganmi?

### Kod Sifati
- [ ] DRY — nusxalangan kod (save_json, month_start, _safe_name) bitta joyda mi?
- [ ] Naming: snake_case, o'zbekcha matnlar, minimal comment
- [ ] Type hints (Python 3.10+) qo'llanilganmi?

## Kutilgan Natija
- Topilgan muammolar ro'yxati (severity: critical/high/medium/low)
- Tuzatish takliflari (kod parchasi bilan)
- Ustuvorlik tartibida saralash