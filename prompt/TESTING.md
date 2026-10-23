# Testing Prompt

## Maqsad
Testlarni yozish, ishga tushirish va debug qilish.

## Test Tuzilmasi

```
tests/
├── conftest.py              # Fixtures, bot_settings isolation, SQLite backend
├── sqlite_backend.py        # Storage-compatible SQLite replica
├── test_dashboard*.py       # Dashboard API + UI tests
├── test_ops_bot.py          # Operations bot tests
├── test_driver_schedule_bot.py
├── test_db.py               # DB storage tests
├── test_*.py                # Boshqa modul testlari
```

## Test Ishga Tushirish

```bash
# Full suite (~540 tests, ~3.5 min)
python -X utf8 -m pytest tests/ -q

# Single file
python -X utf8 -m pytest tests/test_dashboard.py -q

# Single test
python -X utf8 -m pytest tests/test_dashboard.py::test_api_summary -q -v

# Keyword filter
python -X utf8 -m pytest tests/test_driver_schedule_bot.py -q -k group

# With coverage
python -X utf8 -m pytest tests/ --cov=bm_automation --cov-report=term-missing
```

## Muhim Fixtures (conftest.py)

- `storage` — SQLite Storage instance (auto-generated schema)
- `seeded_storage` — Pre-populated with test data
- `monkeypatch` — For patching module internals

### Patch Target Rules
> **Patch the name in the module that USES it**, not where it's defined.
> ```python
> # ✅ To'g'ri
> monkeypatch.setattr(daily_summary, "get_storage", ...)
> 
> # ❌ Noto'g'ri
> monkeypatch.setattr(storage, "get_storage", ...)
> ```

### Interval Counter Reset
Testlarda vaqtga bog'liq funksiya bo'lsa (masalan `_LAST_CHECK_AT`):
```python
monkeypatch.setattr(problem_alerts, "_LAST_CHECK_AT", 0.0)
```

## Dashboard Testlari (test_dashboard.py)

- `_get(path)` / `_post(path, json)` — TestClient helper
- `BM_DB_DRIVER=sqlite BM_TEST_MODE=1` — avtomatik o'rnatiladi
- `scope` dependency → testda ADMIN session simulyatsiya qilinadi

## Bot Testlari (test_ops_bot.py)

- `dispatch.py` handlerlarini test qilish
- Role-based access tekshirish
- Callback data parsing

## Test Yazish Qoidalari

1. **Arrange-Act-Assert** strukturasi
2. **Isolyatsiya**: har test o'z ma'lumotlarini yaratadi
3. **Tezlik**: `session` scope fixture'lar dan foydalanish
4. **Naming**: `test_<nima>_<qanday_shartda>_<natija>`

## Debug

```bash
# Verbose output
python -X utf8 -m pytest tests/test_dashboard.py::test_api_summary -v -s

# Print debug
python -X utf8 -m pytest tests/test_dashboard.py -q -s --tb=long

# PDB
python -X utf8 -m pytest tests/test_dashboard.py::test_api_summary --pdb
```

## CI (GitHub Actions)

`.github/workflows/test.yml`:
- Python 3.12
- `BM_DB_DRIVER=sqlite BM_TEST_MODE=1`
- `pytest tests -q`

## Xatolarni Tahlil

| Xato | Sabab | Yechim |
|------|-------|--------|
| `KeyError: 'ok'` | Auth bypass ishlamadi | `BM_TEST_MODE=1` + loopback check |
| `PermissionError` log rotate | Windows file lock | Testdan keyin log faylni yopish |
| `ModuleNotFoundError` | Import path | `PYTHONPATH=.` yoki `-m` |