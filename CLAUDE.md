# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**BM Automation** — Transport pipeline for bus companies (Fergana). Pulls daily trips from the BM API (`bm.dtransport.uz`), renders driver sheets, sends via a Telegram bot, produces monthly summary, writes back to the site. Uzbek-owned; **all user-facing Telegram/dashboard text is in Uzbek (Latin)**. Code style: snake_case, minimal comments.

**Architecture**: Monorepo with two coexisting layers:
- **Legacy modules** (root `bm_automation/*.py`): `bot.py`, `daily.py`, `client.py`, `notify.py`, `profiles.py`, `state.py`, `tokens.py`, `duty.py`, `driver_sheet.py`, etc. — compatibility shims
- **Refactored `bm_automation/app/` layer** (prefer this): `notifications/ops/`, `dashboard/`, `exporters/`, `db/`, `core/`, `myai/`, `scheduler/`, `services/`, `utils/`, `repositories/`

---

## Environment (Windows — Critical)

| Item | Value |
|------|-------|
| **Repo path** | `C:\Users\User\OneDrive\Докуменties\Default Project` (spaces + Cyrillic) |
| **Python interpreter** | `C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe` |
| **Required flag** | `-X utf8` on every invocation |
| **CI Python** | 3.12 (local is 3.14) |
| **Secrets** | `.env` (gitignored) — never commit or log |

```powershell
# Always use full path
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m pytest tests/ -q
```

---

## Commands

### Test Suite
```powershell
# Full suite (~540 tests, ~3.5 min)
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m pytest tests/ -q

# Single file / single test
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m pytest tests/test_driver_schedule_bot.py -q -k group

# Dashboard tests only
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m pytest tests/test_dashboard*.py -q

# Syntax check
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m py_compile bm_automation/app/notifications/ops/ops.py
```

### Run Services
```powershell
# Bot (via bot.bat, uses pythonw.exe for no window)
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -u -m bm_automation bot --watchdog

# Dashboard (FastAPI + uvicorn, port 8080)
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -u -m bm_automation dashboard --port 8080 --no-browser

# Managed by servislar.ps1
.\servislar.ps1 -Action restart|status|start|stop
```

### Dashboard Development
```powershell
# Dashboard is single-file SPA: bm_automation/app/dashboard/web/index.html
# After editing inline JS: extract + node --check
# Deploy: scp index.html to VDS (StaticFiles serves from disk on every request)
```

### Database
- **Production**: PostgreSQL (docker-compose `db` service)
- **Tests**: SQLite via `BM_DB_DRIVER=sqlite BM_TEST_MODE=1` (auto-isolated by `tests/conftest.py`)
- **Schema**: `bm_automation/app/db/schema.py` → tables; `storage.py` implementations

---

## Architecture / Key Directories

```
bm_automation/
├── app/
│   ├── notifications/ops      # Telegram Operations Bot
│   │   ├── ops.py              # main, poll_forever, _handle_update, auto-agents
│   │   ├── dispatch.py         # handle_message / handle_callback (role-gated)
│   │   ├── roles.py            # Role (ADMIN/DISPATCHER/MANAGER/VIEWER), resolve_role
│   │   ├── render.py           # text/keyboard rendering
│   │   ├── context.py          # filters (profile bus)
│   │   └── auto-agents/        # problem_alerts, doc_expiry, monthly_results, fines_report, daily_summary, self_review, grafik_sms, group_departures
│   ├── dashboard/              # FastAPI + Single-file SPA
│   │   ├── fastapi_app.py      # app factory, CORS, middleware, static files
│   │   ├── deps.py             # auth, scope, rate-limit, sessions, passwords
│   │   ├── metrics.py          # business logic (summary, brutto, attendance, etc.)
│   │   ├── routers/            # auth, data, drivers, admin, system
│   │   └── web/index.html      # 7800+ lines: HTML + CSS + JS (inline)
│   ├── core/                   # bot_users, group_stats, bot_settings, companies, profiles, tokens, state
│   ├── db/                     # storage, schema, repositories
│   ├── exporters/              # sheet_image, Excel/PDF
│   ├── myai/                   # optional LLM multi-agent (own schema/state)
│   └── gps/                    # GPS/ZLMediaKit integration
├── cli/                        # CLI entrypoints
└── (legacy root modules)       # compatibility shims → prefer app/
```

---

## Bot Behavior (Know Before Editing)

- **Roles**: Allow-list based (`TG_ADMIN_IDS`, `TG_DISPATCHER_IDS`, `TG_MANAGER_IDS`, `TG_DRIVER_IDS`, `TG_ALLOWED_IDS`)
- **Resolution**: `chat_id` for private chats; `from.id` inside groups (`_update_sender`)
- **Group mode**: Only answers slash-commands / `@bot` mentions / replies-to-bot; replies only to ADMIN/DISPATCHER/MANAGER; short text only (`/today` → `render.today_short`)
- **New-user notification** (`_notify_new_user`): Only for real named private chats
- **`send_message` signature**: `send_message(text, chat_id=None, ...)` — text is FIRST positional arg
- **Auto-agents** (`AI_AUTO_AGENTS=on`): Run in `poll_forever()`; individual agents gated by `AI_DAILY_SUMMARY`, `AI_SELFREVIEW`, `FINES_REPORT`
- **Exception**: `group_departures.check_and_send()` runs every poll loop regardless — gated by `GROUP_DEPARTURE_NOTIFY` / per-chat `/chiqish_on|off`

---

## Dashboard Key Points

- **Multi-company auth (2026)**: `dashboard_users` table (PBKDF2-SHA256), httpOnly `bm_session` cookie (12h), role matrix DISPATCHER<MANAGER<DIRECTOR<ADMIN
- **Finance hidden from DISPATCHER**: `PAGE_ROLES` (frontend) + `_GET_ROLE_MIN` (server) + `_strip_finance()` on `/api/routes`, `/api/summary`
- **Bootstrap ADMIN**: `DASHBOARD_ADMIN_USER/PASS` → auto-create on first run if table empty
- **Rate-limit**: 1 sec / endpoint on POST; `/api/dashboard-users` update/delete by `id` or `username`
- **StaticFiles**: `index.html` served from disk on every request — deploy = `scp`, no restart needed

---

## Testing Quirks

- `tests/conftest.py`: Auto-isolates `bot_settings` (km_rate forced to 0.0)
- `tests/sqlite_backend.py`: SQLite replica with auto-generated schema from `app/db/schema.py`
- **Patch target**: Patch the name in the module that USES it (e.g. `monkeypatch.setattr(daily_summary, "get_storage", ...)`)
- Reset interval counters like `_LAST_CHECK_AT` when testing scheduling
- Dashboard tests in `tests/test_dashboard.py` — after editing `web/index.html`, validate inline JS (`node --check`)

---

## Git Workflow

- Local repo, remote `origin` = GitHub, branch `master`
- **Never `git push` unless explicitly asked**
- Commit only when asked; messages in repo's existing style (short, Uzbek or English)
- After editing `.env` or bot code → bot process must be restarted (`load_dotenv()` runs once at import)

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `AGENTS.md` | Project instructions (this file is based on it) |
| `DEPLOY_VDS.md` | VDS deploy guide |
| `bm_automation/app/dashboard/DESIGN.md` | Design system docs |
| `bm_automation/app/dashboard/deps.py` | Auth/scope/sessions/rate-limit |
| `bm_automation/app/dashboard/fastapi_app.py` | FastAPI factory, middleware, CORS, static files |
| `bm_automation/app/notifications/ops/ops.py` | Bot main loop, auto-agents, update handling |
| `bm_automation/app/core/tenant.py` | Multi-company scope, company_state, route filtering |
| `bm_automation/app/db/storage.py` | Storage abstraction (PostgreSQL + SQLite) |

---

## Recent Security Fixes (Applied)

1. **CORS**: `allow_origins=["*"]` → dynamic from `DASHBOARD_CORS_ORIGINS` env (defaults to localhost)
2. **Token injection**: Removed `DASHBOARD_TOKEN` from HTML body; httpOnly cookie only
3. **TEST_MODE bypass**: Now restricted to loopback (`127.0.0.1`) + `testclient` (TestClient)
4. **File handles**: SSE log streaming closes handles in `finally` block
5. **ENV settings**: Critical secrets (`BM_PASSWORD`, `TG_BOT_TOKEN`, `OPENROUTER_API_KEY`, etc.) — only SUPER ADMIN can change via `/api/settings/env`

---

## Dashboard Design System (2026)

- **Tokens**: 3-layer (primitive → semantic → component) — `:root` + `[data-theme="dark"]`
- **Palette**: Indigo + Teal + Neon glow (`--glow`, `--glow-teal`, `--glow-amber`, `--glow-alert`)
- **Glassmorphism**: `var(--bg-glass)` + `backdrop-filter: blur()` + `var(--line-strong)` borders
- **Components**: KPI (neon status bars), Panel, Button, Input, Pill, Switch, Tables, Navigation, Auth Card, Dialog, FAB, Skeleton, Floating Labels, Custom Checkbox/Radio
- **Typography**: Big Shoulders Display (signage), IBM Plex Sans/Mono (UI)
- **Icons**: Feather SVG via inline `<i class="ic" data-ic="...">` system
- **Location**: `bm_automation/app/dashboard/web/index.html` + `DESIGN.md`

---

## Development Notes

- **Inline JS validation**: Always run `node --check` on extracted script blocks after editing `index.html`
- **Icon check**: All `data-ic` values must exist in `FEATHER` dictionary
- **Element IDs / JS functions**: Don't rename without updating callers (SPA wiring at `index.html:5553+`)
- **Themes**: Light/dark via `localStorage['bm-theme']` + `html[data-theme="light"]`
- **Accessibility**: Focus visible, touch targets ≥42px, contrast checked, reduced motion respected

---

## VDS Deploy (Summary)

- **Server**: `vps12327.eskiz.uz` (189.74.100.25), root, key `~/.ssh/bm_vps_ed25519`
- **Dashboard files**: `/opt/bm/bm_automation/app/dashboard/web/`
- **Service**: `bm-dashboard.service` (FastAPI + uvicorn, port 8080, nginx 443 proxy)
- **Deploy**: `scp index.html` → verify md5 → service restarts auto-read from disk
- **Live URL**: `https://bmbrutto.uz/`

---

## Files Removed from Earlier Copy

The following files were part of the earlier compaction and have been removed from this version:
- `C:\Users\User\OneDrive\Документы\Default Project\AGENTS.md`

Original `AGENTS.md` is retained and referenced — do not recreate or overwrite it.