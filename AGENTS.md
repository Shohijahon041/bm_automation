# AGENTS.md

Transport pipeline for bus companies (Fergana). Pulls daily trips from the BM API (`bm.dtransport.uz`), renders driver sheets, sends via a Telegram bot, produces monthly summary, writes back to the site. Uzbek-owned; **code comments/docstrings and all user-facing Telegram/dashboard text are in Uzbek (Latin)**. Code style: snake_case, minimal comments.

## Environment (Windows, critical)

- Repo lives in a OneDrive path with spaces + Cyrillic: `C:\Users\User\OneDrive\Документы\Default Project`. Always pass it as `workdir` (or quote paths); never hardcode the relative path in scripts.
- `python`/`py` are NOT usable from this shell (aliases not found). Use the full interpreter path:
  `C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe`
- Add `-X utf8` to every python invocation (Uzbek/Cyrillic output). CI runs Python 3.12; local is 3.14.
- `.env` holds all secrets (BM creds, Telegram tokens, API keys, DASHBOARD_TOKEN). It is gitignored — **never commit or log it**. `variable_name` in `.env` → read via `os.getenv` in `bm_automation/app/config/settings.py::telegram_settings()`.

## Commands

```powershell
# Full test suite (≈540 tests, ~3.5 min)
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m pytest tests/ -q
# Single file / single test
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m pytest tests/test_driver_schedule_bot.py -q -k group
# Syntax check a module
& "C:\...\pythoncore-3.14-64\python.exe" -X utf8 -m py_compile bm_automation/app/notifications/ops/ops.py

# Chrome DevTools / Node NOT available unless node is installed; if editing dashboard
# web/index.html JS, extract inline <script> blocks to a temp file and run `node --check`.
```

### Running the services

- Bot: `python.exe -X utf8 -u -m bm_automation bot --watchdog` (passed via `bot.bat`; `pythonw.exe` for no window). `--once` = single getUpdates poll for a smoke test.
- Dashboard: `python.exe -X utf8 -u -m bm_automation dashboard --port 8080 --no-browser` (stdlib `http.server`, no web framework).
- Managed by `servislar.ps1 -Action restart|status|start|stop` (both via `bot.bat`/`dashboard.bat` + `run_hidden.vbs`).
- Process identity for restart: `Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"` filtering CommandLine for `bm_automation (bot|dashboard)`.
- Logs: bot → `state/bot_stdout.log`, `state/bot_stderr.log`; dashboard → `state/dashboard_console.log`; docs/logs → `logs/bm.log`.

**After editing `.env` or bot code, the bot process must be restarted** (`load_dotenv()` runs once at import; `os.environ` is not re-read from disk).

## Architecture / layout

- Package is `bm_automation/`. CLI entrypoints: `bm_automation/cli/parser.py` (invoked via `python -m bm_automation <cmd>`); old top-level modules (`bot.py`, `daily.py`, `client.py`, `notify.py`, `profiles.py`, `state.py`, `tokens.py`, `duty.py`, `driver_sheet.py`, etc.) and the refactored `bm_automation/app/` layer coexist — **prefer `app/`; legacy modules are compatibility shims** (several still point into `app/`).
- `bm_automation/app/`:
  - `notifications/ops/` — the Telegram Operations Bot:
    - `ops.py` — `main`, `poll_forever` (long-poll), `_handle_update` (private vs group routing), `_notify_new_user`, `_auto_agents_enabled`.
    - `dispatch.py` — `handle_message` / `handle_callback` (role-gated); big file, keep ordering by callback data prefix.
    - `roles.py` — `Role` (ADMIN/DISPATCHER/MANAGER/VIEWER), `resolve_role`, `is_allowed`, `configured_roles`.
    - `render.py` — all text/keyboard rendering; `kb.py` keyboards; `context.py` filters (profile bus).
    - Auto-agents (scheduled senders): `problem_alerts.py`, `doc_expiry.py`, `monthly_results.py`, `fines_report.py`, `daily_summary.py`, `self_review.py` (AI `/insights`), `grafik_sms.py`.
  - `dashboard/` — `metrics.py`, `server.py`, `web/index.html` (single-file SPA, inline JS + CSS).
    - Ko'p korxonali auth (2026): `dashboard_users` jadvali (pbkdf2-sha256), httpOnly `bm_session` cookie (12 soat, `state/dashboard_sessions.json`), ro`l matritsasi DISPATCHER<MANAGER<DIRECTOR<ADMIN; non-admin faqat o'z korxonasini ko'radi (`company` = profil nomi; `routeVariantId` scope, `_force_route`).
    - Bootstrap ADMIN: `DASHBOARD_ADMIN_USER/PASS` (`.env`) → jadval bo'sh bo'lsa birinchi ishga tushishda avtomatik yaratiladi; keyingi adminlar Settings > "Dashboard foydalanuvchilari" orqali.
    - POST endpointlarda rate-limit (1 sek / endpoint) va `/api/dashboard-users` update/delete `id` yo'q bo'lsa `username` bilan topadi (frontend faqat username yuboradi).
  - `exporters/` — `sheet_image.py` (driver schedule PNG), Excel/PDF exporters.
  - `db/` — storage layer; `schema.py` defines tables; `storage.py` implementations.
  - `core/` — `bot_users.py`, `group_stats.py`, `bot_settings.py`, `companies.py`, `profiles.py`, `tokens.py`, `state.py`.
  - `myai/` — optional LLM multi-agent system (own schema/state).
- State JSON lives in `state/` (`bot_users.json`, `group_stats.json`, `daily_summary.json`, `problem_alerts.json`, `self_review.json`, `driver_entry.json`, `registration.json`, …). All gitignored.

## Bot behavior — know before editing

- Roles are allow-list based (`TG_ADMIN_IDS`, `TG_DISPATCHER_IDS`, `TG_MANAGER_IDS`, `TG_DRIVER_IDS`, `TG_ALLOWED_IDS`) resolved by **chat_id for private chats**, but by **sender `from.id` inside groups** (`_update_sender`).
- Group mode (`ops._handle_group_update`): bot only answers slash-commands / `@bot` mentions / replies-to-bot; replies only to ADMIN/DISPATCHER/MANAGER; group answers are short text only (`/today` → `render.today_short`, `/grafik` → short note, NO full card/Excel/PNG). Group messages are recorded in `group_stats.json` (`record_group_message`), **not** in `bot_users.json`.
- New-user notification (`_notify_new_user`) fires only for real named private chats (`uinfo.first_name`/`username` non-empty); channel posts / `my_chat_member` / unnamed updates are skipped to avoid spam.
- **`send_message` signature gotcha**: it is `send_message(text, chat_id=None, ...)` — the FIRST positional arg is the text, chat_id is keyword/named. `reply` helpers in dispatch follow `reply(chat_id, text)`.
- Auto-agents in `poll_forever()` only run when `AI_AUTO_AGENTS=on` (`auto_agents` in settings; default `off` = bot silent except on commands). Individual agents additionally gated by `AI_DAILY_SUMMARY`, `AI_SELFREVIEW`, `FINES_REPORT`.

## Testing quirks

- `tests/conftest.py` auto-isolates `bot_settings` (km_rate forced to 0.0) so live `state/bot_settings.json` can't leak into tests.
- `tests/sqlite_backend.py` is a `Storage`-compatible SQLite replica with schema auto-generated from `app/db/schema.py` — production only uses PostgreSQL. Reuse it via the `storage` fixture (`storage_for(SQLiteDatabase(...))`).
- CI (`.github/workflows/test.yml`) runs `pytest tests -q` with `BM_DB_DRIVER=sqlite` and `BM_TEST_MODE=1` on Python 3.12. When patching module internals in tests, patch the name in the module that USES it (e.g. `monkeypatch.setattr(daily_summary, "get_storage", ...)`), and reset module interval counters like `_LAST_CHECK_AT` when testing scheduling.
- Dashboard tests live in `tests/test_dashboard.py`. After editing `web/index.html`, validate the inline JS (extract + `node --check`) and don't rename existing element IDs / JS function names without updating callers (SPA wiring at `index.html:5553+`).

## Git workflow

- Local repo, remote `origin` = GitHub. Branch `master`. **Never `git push` unless explicitly asked.** Commit only when asked; write messages in the repo's existing style (short, Uzbek or English fits).