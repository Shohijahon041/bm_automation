"""Kompaniya tokenlarini yangilash tarixi (`state/company_tokens.json`).

`bm-token-refresh` timer'i va dashboarddagi qo'lda yangilash shu faylga
natija yozadi: oxirgi urinish, oxirgi muvaffaqiyatli refresh va xato matni.
Token qiymati (access/refresh) bu faylga hech qachon yozilmaydi.

Barcha funksiyalar best-effort: yozish imkonsiz bo'lsa xato ko'tarilmaydi —
token yangilanishining o'zi panel holatiga bog'lanib qolmasligi kerak.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..utils.io import atomic_write

STATE_FILE = Path("state") / "company_tokens.json"
MAX_ERROR = 300
_SUCCESS = {"refreshed", "relogin", "ok"}


def _norm(name: str) -> str:
    """Firma nomini kalitga keltiradi (registr/bo'shliq farqi hisobsiz)."""
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("companies"), dict):
            return data
    except Exception:  # noqa: BLE001 - buzilgan/o'qilmaydigan fayl = bo'sh tarix
        pass
    return {"companies": {}}


def _save(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(STATE_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def record(name: str, result: str, error: str = "") -> None:
    """Bitta kompaniya urinishi natijasini yozadi (best-effort).

    `result`: `refreshed` / `relogin` / `ok` — muvaffaqiyat, aks holda xato.
    Har urinishda `last_attempt_at` yangilanadi; `last_ok_at` faqat
    muvaffaqiyatli urinishda.
    """
    key = _norm(name)
    if not key:
        return
    try:
        data = _load()
        rec = data["companies"].get(key) or {"name": str(name).strip()}
        now = datetime.now().isoformat(timespec="seconds")
        ok = str(result or "") in _SUCCESS
        rec["name"] = str(name).strip()
        rec["last_attempt_at"] = now
        rec["last_result"] = "ok" if ok else "error"
        if ok:
            rec["last_ok_at"] = now
            rec["last_ok_result"] = str(result)
            rec["last_error"] = ""
            rec["consecutive_errors"] = 0
        else:
            rec["last_error"] = " ".join(str(error or "").split())[:MAX_ERROR]
            rec["consecutive_errors"] = int(rec.get("consecutive_errors") or 0) + 1
        data["companies"][key] = rec
        data["updated_at"] = now
        _save(data)
    except Exception:  # noqa: BLE001 - tarix yozilmasa keep-alive davom etadi
        pass


def get(name: str) -> dict:
    """Kompaniyaning saqlangan tarixi (yo'q bo'lsa bo'sh qiymatlar)."""
    rec = _load()["companies"].get(_norm(name)) or {}
    return {
        "last_attempt_at": str(rec.get("last_attempt_at") or ""),
        "last_ok_at": str(rec.get("last_ok_at") or ""),
        "last_ok_result": str(rec.get("last_ok_result") or ""),
        "last_result": str(rec.get("last_result") or ""),
        "last_error": str(rec.get("last_error") or ""),
        "consecutive_errors": int(rec.get("consecutive_errors") or 0),
    }
