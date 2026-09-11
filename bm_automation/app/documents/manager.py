"""Xujjatlar boshqaruvchisi — CRUD (yaratish, o'qish, yangilash, o'chirish).

Xujjatlar jadvalida saqlanadi va dashboard API orqali boshqariladi.
"""

from __future__ import annotations

import json
from typing import Any

from ..utils.logger import get_logger
from ..db.storage import get_storage
from ..db.models import json_dumps, json_loads, now_utc
from .engine import (
    render_template, render_html, create_document,
    generate_ai_content, validate_fields,
)
from .templates import TEMPLATES, get_template, list_templates

log = get_logger("bm_automation.documents")


class DocumentManager:
    """Xujjatlar CRUD boshqaruvchisi."""

    def __init__(self):
        self.storage = get_storage()

    # ------------------------------------------------------------------ CRUD

    def list_documents(self, category: str = "", status: str = "",
                       search: str = "", driver_id: str = "",
                       limit: int = 50) -> list[dict]:
        """Xujjatlar ro'yxatini qaytaradi."""
        where, params = [], []
        if category:
            where.append("category=%s")
            params.append(category)
        if status:
            where.append("status=%s")
            params.append(status)
        if driver_id:
            where.append("driver_id=%s")
            params.append(driver_id)
        if search:
            where.append("(title ILIKE %s OR body_text ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        sql = "SELECT * FROM documents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"

        rows = self.storage.query(sql, tuple(params), limit=limit)
        for row in rows:
            row["fields"] = json_loads(row.get("fields", "{}"))
            row["attachments"] = json_loads(row.get("attachments", "[]"))
        return rows

    def get_document(self, doc_id: int) -> dict | None:
        """Bitta xujjatni qaytaradi."""
        row = self.storage.find("documents", id=doc_id)
        if row:
            row["fields"] = json_loads(row.get("fields", "{}"))
            row["attachments"] = json_loads(row.get("attachments", "[]"))
        return row

    def create_document(self, template_key: str, fields: dict[str, str],
                        title: str = "", status: str = "draft",
                        driver_id: str = "",
                        attachments: list[str] | None = None) -> dict[str, Any]:
        """Yangi xujjat yaratadi va DB ga saqlaydi."""
        template = get_template(template_key)
        if not template:
            return {"ok": False, "error": f"Shablon topilmadi: {template_key}"}

        errors = validate_fields(template, fields)
        if errors:
            return {"ok": False, "error": "Validatsiya xatolari", "errors": errors}

        body_text = render_template(template, fields)
        body_html = render_html(template, fields)

        now = now_utc()
        self.storage.insert("documents", {
            "title": title or template.name,
            "template_key": template_key,
            "category": template.category,
            "driver_id": driver_id,
            "fields": json_dumps(fields),
            "body_text": body_text,
            "body_html": body_html,
            "status": status,
            "attachments": json_dumps(attachments or []),
        })

        return {"ok": True, "message": f"'{title or template.name}' yaratildi"}

    def update_document(self, doc_id: int, fields: dict[str, str] | None = None,
                        title: str | None = None, status: str | None = None,
                        attachments: list[str] | None = None) -> dict[str, Any]:
        """Xujjatni yangilaydi."""
        existing = self.get_document(doc_id)
        if not existing:
            return {"ok": False, "error": "Xujjat topilmadi"}

        updates = {}

        if title is not None:
            updates["title"] = title

        if status is not None:
            updates["status"] = status

        if attachments is not None:
            updates["attachments"] = json_dumps(attachments)

        if fields is not None:
            template = get_template(existing.get("template_key", ""))
            if template:
                merged = {**existing.get("fields", {}), **fields}
                errors = validate_fields(template, merged)
                if errors:
                    return {"ok": False, "error": "Validatsiya xatolari", "errors": errors}

                updates["fields"] = json_dumps(merged)
                updates["body_text"] = render_template(template, merged)
                updates["body_html"] = render_html(template, merged)

        if updates:
            sets = ", ".join(f"{k}=%s" for k in updates)
            self.storage.db.execute(
                f"UPDATE documents SET {sets}, updated_at=%s WHERE id=%s",
                tuple(updates.values()) + (now_utc(), doc_id),
            )

        return {"ok": True, "message": "Xujjat yangilandi"}

    def delete_document(self, doc_id: int) -> dict[str, Any]:
        """Xujjatni o'chiradi."""
        existing = self.get_document(doc_id)
        if not existing:
            return {"ok": False, "error": "Xujjat topilmadi"}

        self.storage.db.execute(
            "DELETE FROM documents WHERE id=%s", (doc_id,)
        )
        return {"ok": True, "message": f"'{existing.get('title', '')}' o'chirildi"}

    # ------------------------------------------------------------- AI

    def ai_generate(self, template_key: str, context: dict[str, str],
                    custom_prompt: str = "") -> dict[str, Any]:
        """AI yordamida xujjat generatsiya qiladi."""
        template = get_template(template_key)
        if not template:
            return {"ok": False, "error": f"Shablon topilmadi: {template_key}"}

        result = generate_ai_content(template, context, custom_prompt)
        return {"ok": True, **result}

    # ------------------------------------------------------------- EXPORT

    def get_html(self, doc_id: int) -> str | None:
        """Xujjatning HTML versiyasini qaytaradi."""
        doc = self.get_document(doc_id)
        if not doc:
            return None
        return doc.get("body_html", "")

    def export_text(self, doc_id: int) -> str | None:
        """Xujjatning matn versiyasini qaytaradi."""
        doc = self.get_document(doc_id)
        if not doc:
            return None
        return doc.get("body_text", "")

    # ------------------------------------------------------------- STATS

    def stats(self) -> dict[str, int]:
        """Xujjatlar statistikasi."""
        if not self.storage.enabled:
            return {"total": 0, "draft": 0, "approved": 0, "archived": 0}

        rows = self.storage.query(
            "SELECT status, COUNT(*) as n FROM documents GROUP BY status"
        )
        stats = {"total": 0, "draft": 0, "approved": 0, "archived": 0}
        for row in rows:
            status = row.get("status", "")
            count = row.get("n", 0)
            stats["total"] += count
            if status in stats:
                stats[status] = count
        return stats

    # ------------------------------------------------------------- ATTACHMENTS

    def attach_file(self, doc_id: int, file_path: str) -> dict[str, Any]:
        """Xujjatga fayl biriktiradi."""
        doc = self.get_document(doc_id)
        if not doc:
            return {"ok": False, "error": "Xujjat topilmadi"}

        attachments = doc.get("attachments", [])
        if file_path not in attachments:
            attachments.append(file_path)
            self.storage.db.execute(
                "UPDATE documents SET attachments=%s, updated_at=%s WHERE id=%s",
                (json_dumps(attachments), now_utc(), doc_id),
            )
        return {"ok": True, "message": "Fayl biriktirildi"}

    def detach_file(self, doc_id: int, file_path: str) -> dict[str, Any]:
        """Xujjatdan faylni olib tashlaydi."""
        doc = self.get_document(doc_id)
        if not doc:
            return {"ok": False, "error": "Xujjat topilmadi"}

        attachments = doc.get("attachments", [])
        if file_path in attachments:
            attachments.remove(file_path)
            self.storage.db.execute(
                "UPDATE documents SET attachments=%s, updated_at=%s WHERE id=%s",
                (json_dumps(attachments), now_utc(), doc_id),
            )
        return {"ok": True, "message": "Fayl olib tashlandi"}


# Singleton
_manager: DocumentManager | None = None


def get_manager() -> DocumentManager:
    global _manager
    if _manager is None:
        _manager = DocumentManager()
    return _manager
