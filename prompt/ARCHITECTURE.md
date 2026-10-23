# Architecture Prompt

## Maqsod
Loyiha arxitekturasini tushunish va kengaytirish.

## Qavatlar (Layers)

```
┌─────────────────────────────────────────────────────────────┐
│  PRESENTATION (CLI / Bot / Dashboard)                       │
├─────────────────────────────────────────────────────────────┤
│  SERVICES (Business Logic)                                  │
│  daily, duty, gross, waybill, report, summary, export,     │
│  driver_sheet, excel_fill, stats, grafik_sms               │
├─────────────────────────────────────────────────────────────┤
│  CORE (Shared Domain)                                       │
│  companies, profiles, tokens, state, bot_settings, tenant  │
├─────────────────────────────────────────────────────────────┤
│  DATA ACCESS                                                │
│  db/storage.py (Storage abstraction)                        │
│  db/schema.py (Tables)                                      │
│  repositories/ (GrossRepository, etc.)                      │
├─────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE                                             │
│  api/client.py (BMClient), notifications/telegram.py,      │
│  utils/logger.py, utils/io.py (atomic_write)               │
└─────────────────────────────────────────────────────────────┘
```

## Muhim Abstraksiyalar

| Abstraksiya | Fayl | Vazifa |
|-------------|------|--------|
| `Storage` | `db/storage.py` | PG/SQLite unified CRUD + query |
| `BMClient` | `api/client.py` | BM API HTTP + auth/refresh + retry |
| `Tenant` | `core/tenant.py` | Multi-company scope, company_state |
| `Scope` | `dashboard/deps.py` | Auth + role + route filtering |
| `Logger` | `utils/logger.py` | Rotating file + levels + console |

## Yangi Servis Qo'shish

```
bm_automation/app/services/yangi_servis.py
├── class YangiServis:
│   def __init__(self, storage=None):
│       self.storage = storage or get_storage()
│
│   def bajar(self, params):
│       # Business logic
│       return natija
```

Keyin `bm_automation/app/services/__init__.py` ga export qo'shish.

## Multi-Company Scope

```python
# Dashboard deps.py da
scope = await resolve_scope(request)
# scope = {
#   "username": "...",
#   "role": "MANAGER",
#   "company": "Farg'ona Avtobus",
#   "is_admin": False,
#   "company_id": 5,
#   "routes": ["rv-123", "rv-456"]  # Faqat shu yo'nalishlar
# }

# force_route() — non-admin uchun route paramni kesadi
params = force_route(scope, "/api/summary", params)
```

## Async Patterns

```python
# ✅ To'g'ri: async def + await
async def fetch_data():
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()

# ❌ Noto'g'ri: sync blocking async ichida
async def fetch_data():
    return requests.get(url).json()  # BLOKSLAYDI!
```

## Dependency Injection Pattern

```python
# services/__init__.py
def get_daily_service(storage=None):
    return DailyService(storage or get_storage())

# Routerda
async def api_summary(request, scope, service: DailyService = Depends(get_daily_service)):
    return service.summary(params)
```

## Configuration Flow

```
.env → config/settings.py (Config class) → get_config()
                    ↓
         telegram_settings(), openrouter_settings(), db_settings()
                    ↓
         bot_settings.py (runtime settings: km_rate, elec_price, etc.)
```

## Error Handling Pattern

```python
try:
    result = await risky_operation()
except BMClient.RateLimitError:
    await asyncio.sleep(backoff)
    return await retry()
except StorageError as e:
    log.error("DB xato: %s", e)
    raise HTTPException(500, "Ma'lumotlar bazasi xatosi")
except Exception as e:
    log.exception("Kutilmagan xato")
    raise HTTPException(500, "Ichki xato")
```

## Naming Conventions

| Element | Convention |
|---------|------------|
| Files/modules | `snake_case.py` |
| Classes | `PascalCase` |
| Functions/variables | `snake_case` |
| Constants | `UPPER_SNAKE_CASE` |
| Private | `_leading_underscore` |
| User-facing text | O'zbekcha (Latin) |
| Comments | Minimal, only "why" not "what" |

## State Management

| State | Location | Persistence |
|-------|----------|-------------|
| Bot users | `state/bot_users.json` | JSON file |
| Group stats | `state/group_stats.json` | JSON file |
| Daily summary | `state/daily_summary.json` | JSON file |
| Problem alerts | `state/problem_alerts.json` | JSON file |
| Dashboard sessions | `state/dashboard_sessions.json` | JSON file + DB |
| Tokens | `tokens.json` + `company_tokens/` | JSON file |

## Scheduled Jobs

| Job | Schedule | File |
|-----|----------|------|
| Daily sync | Har kuni 02:00 | `daily.py` / `scheduler/` |
| Grafik yuborish | Har kuni 06:00 | `grafik_sms.py` |
| Chiqish eslatmasi | Har kuni 06:30/12:00 | `group_departures.py` |
| Problem alerts | Har 30 min | `problem_alerts.py` |
| Doc expiry | Har kuni 09:00 | `doc_expiry.py` |
| Monthly results | 1-sana 10:00 | `monthly_results.py` |
| Fines report | Har kuni 14:00 | `fines_report.py` |
| Self review | `AI_SELFREVIEW_HOUR` | `self_review.py` |
| Cleanup | Har kuni 03:00 | `cleanup` command |