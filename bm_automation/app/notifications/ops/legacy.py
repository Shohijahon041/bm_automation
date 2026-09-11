"""Eski bot imkoniyatlari: qayta yuborish, Excel exporti va yuklab to'ldirish.

Bu funksiyalar yangi professional bot'da saqlanib qolgan legacy xatti-
harakatlar — avtomatik rejimlar (resend, sayt exporti, Excel yuklab
to'ldirish) va ularning callback'lari.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from ...config.settings import PROD_BASE_URL, telegram_settings
from ..telegram import (export_keyboard, send_document, send_message, send_photo,
                        telegram_call)

RESEND_LOCK = threading.Lock()

EXPORTS_STORE = Path("reports") / "_exports_pending.json"


def _reply(chat_id: int, text: str, reply_markup=None) -> None:
    try:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        telegram_call("sendMessage", payload)
    except Exception as exc:
        print(f"sendMessage xatosi: {exc}")


# ---------------------------------------------------------------- resend

def do_resend(chat_id, name, routes=None) -> None:
    with RESEND_LOCK:
        try:
            from ...services.daily_service import run_daily
            run_daily(send=True, chat_id=chat_id, only=name, routes=routes,
                      trigger="bot", max_attempts=6, attempt_delay=60)
        except Exception as exc:
            print(f"Resend xatosi: {exc}")
            try:
                telegram_call("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"Qayta yuborish xatosi: {exc}",
                })
            except Exception:
                pass


def _describe(scope) -> str:
    """Resend yo'nalishi tavsifi (xabar uchun)."""
    if not scope:
        return "barcha profillar"
    if isinstance(scope, (list, tuple, set)):
        names = [str(s) for s in scope if str(s).strip()]
        return ", ".join(names) or "barcha profillar"
    return str(scope)


def start_resend(chat_id, name, routes=None) -> None:
    if RESEND_LOCK.locked():
        _reply(chat_id, "Hozir boshqa qayta yuborish ishlayapti. Tugagach qayta bosing.")
        return
    _reply(chat_id, f"Qayta yuborish boshlandi... ({_describe(name)})")
    threading.Thread(target=do_resend, args=(chat_id, name, routes), daemon=True).start()


# ---------------------------------------------------------------- export

def do_export(chat_id, date_arg) -> None:
    try:
        from uuid import uuid4

        from ...core.profiles import get_profile
        from ...services.export_service import build_export

        profile = get_profile("FERGANATEX") or get_profile() or {}
        route = str(profile.get("routeVariantId") or "").strip()
        if not route:
            _reply(chat_id, "Profilda routeVariantId ko'rsatilmagan.")
            return
        pid = str(profile.get("profileId") or "").strip()
        client = ensure_client()
        if pid:
            client.login_by_profile(pid)
        date_str = date_arg
        if not date_str:
            from datetime import date as _date
            date_str = _date.today().isoformat()
        out = build_export(client, route, date_str, Path("reports").resolve(),
                           profile=profile)
        route_name = profile.get("routeName") or route
        stamp = date_str.replace("-", "")
        caption = (f"Yo'nalish Jadvali Export — {route_name} | "
                   f"{datetime.fromisoformat(date_str):%d.%m.%Y}")
        key = "exp" + uuid4().hex[:10]
        register_export(key, str(out), caption)
        send_message(
            f"Yo'nalish Jadvali tayyor ({route_name}, "
            f"{datetime.fromisoformat(date_str):%d.%m.%Y}). "
            "Yuborish tugmasini bosing:",
            chat_id=chat_id, reply_markup=export_keyboard(key))
    except Exception as exc:
        print(f"Export xatosi: {exc}")
        _reply(chat_id, f"Export xatosi: {exc}")


def start_export(chat_id, date_arg) -> None:
    _reply(chat_id, "Export tayyorlanmoqda...")
    threading.Thread(target=do_export, args=(chat_id, date_arg),
                     daemon=True).start()


def ensure_client():
    """PROD token bilan klient; auth buzilsa OneID headless login qiladi."""
    from ...api.client import BMAuthError, BMClient
    from ...config.settings import get_config

    cfg = get_config()
    cfg.base_url = PROD_BASE_URL
    client = BMClient(cfg)
    client.load_tokens_from_file()
    try:
        client.account_authorities()
    except BMAuthError:
        try:
            from ...auth.browser_login import browser_login
            print("Token eskirgan, OneID login...")
            browser_login(headless=True, timeout=300)
        except Exception as exc:
            print(f"OneID login xatosi: {exc}")
        client.load_tokens_from_file()
    return client


# -------------------------------------------------------------- document

def _download_to_temp(file_id: str, dest_dir: Path, filename: str) -> Path:
    s = telegram_settings()
    info = telegram_call("getFile", {"file_id": file_id}) or {}
    file_path = info.get("file_path")
    if not file_path:
        raise RuntimeError("getFile: file_path olinmadi")
    import requests
    url = f"https://api.telegram.org/file/bot{s['token']}/{file_path}"
    resp = requests.get(url, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Fayl yuklab olinmadi: HTTP {resp.status_code}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / filename
    out.write_bytes(resp.content)
    return out


def process_document(chat_id: int, file_id: str, filename: str) -> None:
    try:
        from ...core.profiles import get_profile
        from ...services.excel_fill_service import parse_plan_excel, run

        parsed = parse_plan_excel(
            str(_download_to_temp(file_id, Path("reports/plan"), filename)))
        profile = get_profile() or {}
        route = str(profile.get("routeVariantId") or "").strip()
        if not route:
            _reply(chat_id, "Profilda routeVariantId ko'rsatilmagan.")
            return
        pid = str(profile.get("profileId") or "").strip()
        client = ensure_client()
        if pid:
            client.login_by_profile(pid)
        result = run(client, route, parsed["date"], parsed["rows"],
                     profile=profile, dry_run=False)
        name = profile.get("routeName") or profile.get("name") or route
        caption = f"📋 Jadval to'ldirildi: {name} — {parsed['date']}"
        if result.get("image"):
            send_photo(str(result["image"]), caption=caption, chat_id=chat_id)
        if result.get("excel"):
            send_document(str(result["excel"]),
                          caption=f"📊 To'ldirilgan Excel: {parsed['date']}",
                          chat_id=chat_id)
        _reply(chat_id, "Tayyor.")
    except Exception as exc:
        print(f"Document xatosi: {exc}")
        _reply(chat_id, f"Xato: {exc}")


# ------------------------------------------------------- pending exports

def load_pending_exports() -> dict:
    try:
        from ...utils.cleanup import prune_pending_exports
        prune_pending_exports()
        return json.loads(EXPORTS_STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_pending_exports(data: dict) -> None:
    try:
        EXPORTS_STORE.parent.mkdir(parents=True, exist_ok=True)
        EXPORTS_STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception as exc:
        print(f"Exportlar store saqlanmadi: {exc}")


def register_export(key: str, path: str, caption: str) -> None:
    data = load_pending_exports()
    data[key] = {"path": path, "caption": caption}
    save_pending_exports(data)


def handle_export_callback(chat_id: int, cq: dict, key: str) -> None:
    print(f"Export callback: {key} (chat {chat_id})")
    data = load_pending_exports()
    rec = data.pop(key, None)
    if rec:
        save_pending_exports(data)
    if not rec:
        try:
            telegram_call("answerCallbackQuery", {
                "callback_query_id": cq["id"],
                "text": "Fayl topilmadi yoki allaqachon yuborilgan.",
            })
        except Exception:
            pass
        return
    path = rec.get("path", "")
    if not Path(path).is_file():
        _reply(chat_id, "Export fayli topilmadi.")
        return
    try:
        send_document(path, caption=rec.get("caption", ""), chat_id=chat_id)
        print(f"Export yuborildi: {path}")
    except Exception as exc:
        print(f"Export yuborish xatosi: {exc}")
        _reply(chat_id, f"Fayl yuborilmadi: {exc}")
        return
    try:
        telegram_call("answerCallbackQuery", {
            "callback_query_id": cq["id"],
            "text": "Yuborildi ✓",
        })
        msg = cq.get("message") or {}
        telegram_call("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": "Export yuborildi ✓",
        })
    except Exception:
        pass


# ----------------------------------------------------------------- files

def recent_files(limit: int = 8) -> list[str]:
    out = []
    base = Path("reports")
    if not base.exists():
        return out
    files = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".png", ".xlsx"):
            files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:limit]:
        out.append(f"  {p.relative_to(base).as_posix()} "
                   f"({datetime.fromtimestamp(p.stat().st_mtime):%d.%m %H:%M})")
    return out


def list_text() -> str:
    files = recent_files(limit=20)
    if not files:
        return "📁 <b>SO'NGGI FAYLLAR</b>\n\nFayllar hali yo'q."
    return "📁 <b>SO'NGGI FAYLLAR</b>\n" + "\n".join(files)
