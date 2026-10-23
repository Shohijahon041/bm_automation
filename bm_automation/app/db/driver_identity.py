"""Haydovchi identifikatsiyasi — JSHSHR (PINFL/``tin``) barqaror unikal kalit.

Pasport yoki haydovchilik guvohnomasi almashsa ham JSHSHR o'zgarmaydi, shuning
uchun u shaxsni aniqlaydigan YAGONA identifikator hisoblanadi. Bitta JSHSHR →
bitta ``drivers`` qatori.

BM ba'zan bir shaxsni ikki id ostida qaytaradi (masalan u turli yo'nalishlarda
ishlaganda). Bunday holatda qatorlar birlashtiriladi: **eng boy** qator
(bog'liq ma'lumotlar ko'prog'i, profilida rasmi/telefoni/telegrami bor)
kanonik qator bo'lib qoladi, qolgani o'chirilib bog'liq qatorlar unga
ko'chiriladi.
"""

from __future__ import annotations

from ..utils.logger import get_logger
from .base import Database
from .models import json_loads
from .schema import UNIQUE_KEYS

log = get_logger("bm_automation.db")

TIN_UNIQUE_INDEX = "uq_drivers_tin"

# Profilda qo'shimcha ball beriladigan maydonlar (kanonik qatorni tanlashda).
_PROFILE_BONUS = (
    ("telegram_chat_id", 100),
    ("photo_path", 10),
    ("phone", 5),
    ("license_number", 5),
    ("passport_number", 5),
)


def _driver_ref_tables(db: Database) -> list[str]:
    """``driver_id`` ustuniga ega jadvallar (repoint uchun)."""
    try:
        if getattr(db, "driver", "") == "postgres":
            rows = db.query(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'driver_id'")
            return [str(r.get("table_name") or "") for r in rows if r.get("table_name")]
    except Exception:  # noqa: BLE001
        pass
    # Xavfsiz qaytish (SQLite/test): ma'lum jadvallar.
    return [
        "driver_profiles", "driver_work_logs", "driver_fines", "driver_appeals",
        "duties", "schedules", "trips", "route_daily", "documents", "sms_log",
        "avans",
    ]


def _profile_score(profile: dict) -> int:
    score = 0
    for key, weight in _PROFILE_BONUS:
        if str(profile.get(key) or "").strip():
            score += weight
    return score


def _row_score(storage, row: dict) -> int:
    """Kanonik qatorni tanlash uchun «qanchalik boy» bahosi."""
    eid = str(row.get("external_id") or "")
    score = _profile_score(storage.find("driver_profiles", driver_id=eid) or {})
    ph = storage.db.ph
    for table in _driver_ref_tables(storage.db):
        if table == "driver_profiles":
            continue
        try:
            rows = storage.query(
                f"SELECT COUNT(*) AS n FROM {table} WHERE driver_id={ph}", (eid,))
        except Exception:  # noqa: BLE001
            continue
        if rows:
            score += int(rows[0].get("n") or 0)
    return score


def _merge_profiles(storage, keep: str, lose: str) -> None:
    """Ikkita ``driver_profiles`` qatorini bittaga birlashtiradi (yo'qotmasdan)."""
    keep_row = storage.find("driver_profiles", driver_id=keep) or {}
    lose_row = storage.find("driver_profiles", driver_id=lose) or {}
    if not keep_row and not lose_row:
        return
    if not keep_row:
        # Faqat yo'qotuvchida profil bor → uni kanonikka ko'chiramiz.
        storage.upsert("driver_profiles", ["driver_id"],
                       {**{k: v for k, v in lose_row.items()
                           if k not in ("id", "created_at", "updated_at")},
                        "driver_id": keep})
        return
    if not lose_row:
        return
    # Ikkalasida ham profil: bo'sh maydonlarni yo'qotuvchidan to'ldiramiz.
    merged = dict(keep_row)
    for key, value in lose_row.items():
        if key in ("id", "driver_id", "created_at", "updated_at"):
            continue
        if str(merged.get(key) or "") in ("", "{}", "0", "0.0"):
            if str(value or "") not in ("", "{}", "0", "0.0"):
                merged[key] = value
    keep_row.pop("id", None)
    storage.upsert("driver_profiles", ["driver_id"], merged)
    _delete_row(storage, "driver_profiles", {"driver_id": lose})


def _delete_row(storage, table: str, criteria: dict) -> int:
    where = " AND ".join(f"{k}={storage.db.ph}" for k in criteria)
    try:
        return storage.db.execute(
            f"DELETE FROM {table} WHERE {where}", tuple(criteria.values()))
    except Exception:  # noqa: BLE001
        return 0


def _repoint(storage, keep: str, lose: str) -> int:
    """Yo'qotuvchining bog'liq qatorlarini kanonik id'ga ko'chiradi."""
    moved = 0
    ph = storage.db.ph
    for table in _driver_ref_tables(storage.db):
        if table == "driver_profiles":
            continue
        keys = UNIQUE_KEYS.get(table) or []
        try:
            rows = storage.query(f"SELECT * FROM {table} WHERE driver_id={ph}", (lose,))
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            row_id = row.get("id")
            candidate = {**row, "driver_id": keep}
            # Takroriy qator (masalan bir xil sana) paydo bo'lmasligi uchun.
            if keys and "driver_id" in keys and len(keys) > 1:
                conflict = storage.find(
                    table, **{k: candidate.get(k, "") for k in keys})
                if conflict and conflict.get("driver_id") == keep:
                    _delete_row(storage, table, {"id": row_id})
                    continue
            try:
                storage.db.execute(
                    f"UPDATE {table} SET driver_id={ph} WHERE id={ph}",
                    (keep, row_id))
                moved += 1
            except Exception:  # noqa: BLE001
                _delete_row(storage, table, {"id": row_id})
    return moved


def merge_drivers_by_tin(storage) -> dict:
    """Bir xil JSHSHR'li ``drivers`` qatorlarini birlashtiradi (idempotent).

    Eng boy qator kanonik bo'lib qoladi. Qolgan qatorlar o'chiriladi, ularning
    bog'liq ma'lumotlari (profil/jarima/ish-kuni/hujjat/avans/…) kanonik id'ga
    ko'chiriladi. JSHSHR bo'sh qatorlar tegilmaydi.
    """
    report = {"groups": 0, "merged_rows": 0, "moved": 0}
    if not getattr(storage, "enabled", False):
        return report
    ph = storage.db.ph
    try:
        groups = storage.query(
            f"SELECT TRIM(tin) AS t, COUNT(*) AS n FROM drivers "
            f"WHERE tin <> '' GROUP BY TRIM(tin) HAVING COUNT(*) > 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("Haydovchi dublikatlari o'qilmadi: %s", exc)
        return report
    for group in groups or []:
        tin = str(group.get("t") or "").strip()
        if not tin:
            continue
        rows = storage.query(
            f"SELECT * FROM drivers WHERE TRIM(tin)={ph} ORDER BY id", (tin,))
        if len(rows) < 2:
            continue
        report["groups"] += 1
        keep_row = max(rows, key=lambda r: _row_score(storage, r))
        keep = str(keep_row.get("external_id") or "")
        if not keep:
            continue
        for row in rows:
            lose = str(row.get("external_id") or "")
            if not lose or lose == keep:
                continue
            _merge_profiles(storage, keep, lose)
            report["moved"] += _repoint(storage, keep, lose)
            _delete_row(storage, "drivers", {"external_id": lose})
            report["merged_rows"] += 1
        log.info("Haydovchi birlashtirildi (JSHSHR %s): kanonik %s", tin, keep)
    return report


def merge_drivers_by_alias(storage) -> dict:
    """`data['_aliases']` da ko'rsatilgan, lekin alohida qator bo'lib qolgan
    BM id'larini kanonik qatorga birlashtiradi (idempotent).

    Bunday qatorlar `save_driver` alias kanoniklashtiruvi qo'shilishidan oldin
    yaratilgan bo'lishi mumkin (masalan sinxronizatsiya yoki eski kod). Ularni
    ko'chirib/o'chirib, identifikator birligi saqlanadi.
    """
    report = {"folded": 0, "moved": 0}
    if not getattr(storage, "enabled", False):
        return report
    try:
        rows = storage.query("SELECT external_id, data FROM drivers")
    except Exception:  # noqa: BLE001
        return report
    alias_map: dict[str, str] = {}
    for row in rows or []:
        canon = str(row.get("external_id") or "")
        if not canon:
            continue
        for alias in driver_aliases(row):
            if alias and alias != canon:
                alias_map[alias] = canon
    for alias, canon in alias_map.items():
        if not storage.find("drivers", external_id=canon):
            continue
        if not storage.find("drivers", external_id=alias):
            continue
        _merge_profiles(storage, canon, alias)
        report["moved"] += _repoint(storage, canon, alias)
        _delete_row(storage, "drivers", {"external_id": alias})
        report["folded"] += 1
        log.info("Alias birlashtirildi: %s -> %s", alias, canon)
    return report


def ensure_drivers_tin_unique(storage) -> bool:
    """JSHSHR bo'yicha unikal indeksni ta'minlaydi (dublikat → indeks tartibda)."""
    if not getattr(storage, "enabled", False):
        return False
    try:
        merge_drivers_by_tin(storage)
    except Exception as exc:  # noqa: BLE001
        log.warning("Haydovchi birlashtirish bajarilmadi: %s", exc)
        return False
    try:
        merge_drivers_by_alias(storage)
    except Exception as exc:  # noqa: BLE001
        log.warning("Alias birlashtirish bajarilmadi: %s", exc)
    # TRIM bilan indekslaymiz — bo'shliq/qiymat farqi dublikat yaratmasin.
    sql = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {TIN_UNIQUE_INDEX} "
        f"ON drivers ((TRIM(tin))) WHERE tin <> ''"
    )
    try:
        storage.db.execute(sql, ())
    except Exception as exc:  # noqa: BLE001
        log.warning("JSHSHR unikal indeksi yaratilmadi: %s", exc)
        return False
    log.info("JSHSHR unikal indeksi tayyor (%s)", TIN_UNIQUE_INDEX)
    return True


def driver_aliases(row: dict) -> list[str]:
    """Qatorda yozilgan qo'shimcha BM id'lari (alias) ro'yxati."""
    try:
        data = json_loads(row.get("data")) or {}
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("_aliases") or []
    return [str(a) for a in raw if str(a)]
