# Database Prompt

## Maqsod
Ma'lumotlar bazasi (PostgreSQL/SQLite) bilan ishlash: sxema, migratsiya, query.

## Sxema
**Manba**: `bm_automation/app/db/schema.py` — barcha jadvallar shu yerda.

| Jadval | Maqsad |
|--------|--------|
| `dashboard_users` | Foydalanuvchilar (PBKDF2-SHA256) |
| `dashboard_sessions` | Sessiyalar (JSON faylga ham dubl) |
| `route_daily` | Kunlik reys ma'lumotlari (distance_fact, trip_fact, working_day) |
| `drivers` | Haydovchilar (external_id, route_id, full_name) |
| `driver_work_logs` | Qo'lda kiritilgan ish kunlari |
| `companies` | Firma profillari (sync bilan) |
| `bot_settings` | KM narxi, til, ekr koeffitsiyentlar |
| `fines` | Jarimalar |
| `staff` | Xodimlar (avans, oylik) |
| `appeals` | Murojaatlar |

## Storage Abstraction
`bm_automation/app/db/storage.py` — `Storage` class (PostgreSQL + SQLite unified).

```python
from bm_automation.app.db.storage import get_storage
st = get_storage()

# Yozish
st.dashboard_user_add(username, salt, password_hash, role, company)
st.save_driver(external_id, full_name, route_id)
st.save_route_daily(date, route_id, distance_fact, trip_fact, working_day)

# O'qish
st.dashboard_user_get(username="...")
st.find("drivers", external_id="...")
st.query("SELECT ...", params, limit=10)
```

## Testda SQLite
```bash
BM_DB_DRIVER=sqlite BM_TEST_MODE=1 python -X utf8 -m pytest tests/test_db.py -q
```
- `tests/sqlite_backend.py` — auto-generated schema from `schema.py`
- `tests/conftest.py` — `storage` fixture isolation

## Query Optimizatsiya

| Muammo | Yechim |
|--------|--------|
| N+1 query | `JOIN` yoki batch query |
| Slow `route_daily` | Index: `(date, route_id)` |
| Large JSON | `jsonb` + GIN index |

## Migratsiya
Yangi jadval/ustun qo'shganda:
1. `schema.py` ga qo'shish
2. Testda SQLite avtomatik yaratadi
3. Productionda: `ALTER TABLE` yoki migration tool

## Backup / Restore
```bash
# Backup
pg_dump --data-only --schema=public --no-owner --no-privileges "postgresql://..." > backup.sql

# Restore
docker compose cp backup.sql db:/tmp/dump.sql
docker compose exec db psql -U bm -d bm_loyiha -f /tmp/dump.sql
```