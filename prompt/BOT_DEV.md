# Bot Development Prompt

## Maqsad
Telegram Operations Bot (`app/notifications/ops/`) yaratish / o'zgartirish.

## Kontekst
- Asosiy fayllar: `ops.py`, `dispatch.py`, `roles.py`, `render.py`, `kb.py`, `context.py`, `assistant.py`
- Auto-agents: `problem_alerts.py`, `doc_expiry.py`, `monthly_results.py`, `fines_report.py`, `daily_summary.py`, `self_review.py`, `grafik_sms.py`, `group_departures.py`
- Role: ADMIN / DISPATCHER / MANAGER / VIEWER (allow-list based)

## Asosiy Nuqtalар

### Buyruq qo'shish
1. `dispatch.py` → `handle_message` yoki `handle_callback` ga handler qo'shish
2. Role tekshiruvi: `is_allowed(chat_id, "ROLE")` yoki `resolve_role(chat_id)`
3. Klaviatura: `kb.py` da yaratish
4. Matn: `render.py` da shablon

### Callback data prefix tartibi
```python
# dispatch.py da buyruqlar prefix bo'yicha saralangan:
# "menu_" → "action_" → "set_" → "view_" → "confirm_"
# Yangi callback bu tartibga mos kelishi kerak
```

### Auto-agent qo'shish
1. `app/notifications/ops/` ichida yangi fayl: `yangi_agent.py`
2. `check_and_send()` funksiyasi (har poll tsiklida chaqiriladi)
3. `ops.py` → `poll_forever()` da shartli chaqirish:
```python
if settings.auto_agents_enabled:
    await yangi_agent.check_and_send()
```
4. Alahida config bayrog'i (`AI_YANGI_AGENT=on`) qo'shish

### Role tizimi
- `roles.py`: `Role` enum, `resolve_role(chat_id)`, `is_allowed(chat_id, min_role)`
- Allow-list: `TG_ADMIN_IDS`, `TG_DISPATCHER_IDS`, `TG_MANAGER_IDS`, `TG_DRIVER_IDS`, `TG_ALLOWED_IDS`
- Guruh ichida: `from_id` orqali aniqlash

### Chat turi
- Private: `chat_id` = user ID
- Group: `chat_id` = group ID, lekin `from.id` = user ID

## Xavfsizlik
- `send_message(text, chat_id=None, ...)` — **text 1-pozitsiya**
- `reply(chat_id, text)` — helper
- Rate limit: 1 so'rov/sekunda (BM API)

## Test
```bash
pytest tests/test_ops_bot.py -q -v
pytest tests/test_driver_schedule_bot.py -q -k group
```