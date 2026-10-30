"""Bilim bazasi (Obsidian vault) API — GET ro'yxat/qidiruv/o'qish, POST yozish.

Ruxsat: FAQAT super-admin (tizim egasi). `_GET_ROLE_MIN`/`_POST_ROLE_MIN`
ADMIN (4) talab qiladi, qo'shimcha `scope["is_super"]` tekshiriladi —
boshqa administratorlar bu bo'limni ko'rmaydi. Vault tarkibi ichki qoidalar,
sozlamalar va xato jurnalini o'z ichiga oladi.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..deps import Scope
from ...core import knowledge as vault
from ...core import knowledge_grow
from ...utils.logger import get_logger

log = get_logger("bm_automation.dashboard.knowledge")

router = APIRouter(tags=["knowledge"])


def _require_super(scope: dict) -> None:
    """Bilim bazasi faqat tizim egasi (super-admin) uchun."""
    if not scope.get("is_super"):
        from fastapi import HTTPException
        raise HTTPException(403, "Bilim bazasi faqat tizim egasiga ko'rinadi")


@router.get("/api/knowledge/stats")
def api_knowledge_stats(scope: dict = Depends(Scope())):
    """Vault joyi, qaydlar soni va hajmi."""
    _require_super(scope)
    return vault.stats()


@router.get("/api/knowledge/grow")
def api_knowledge_grow_status(scope: dict = Depends(Scope())):
    """Avtomatik kengaytirish holati (yoqilgan, interval, qaydlar soni)."""
    _require_super(scope)
    return knowledge_grow.status()


@router.post("/api/knowledge/grow")
def api_knowledge_grow(request: Request, scope: dict = Depends(Scope())):
    """Bilim bazasini hoziroq kengaytiradi (`?gaps=0` — bo'shliq to'ldirmasdan).

    Qadam: kod xaritasi, API/bot buyruqlari, testlar, sozlamalar, DB
    jadvallari, holat snapshoti, log tahlili, xato jurnali va yechilmagan
    havolalar uchun bo'sh qaydlar (idempotent).
    """
    _require_super(scope)
    gaps = (request.query_params.get("gaps") or "1").strip().lower()
    res = knowledge_grow.grow(gaps=gaps not in ("0", "off", "false"))
    log.info("Panel: bilim bazasi kengaytirildi (%s qayd)", res.get("notes"))
    return res


@router.get("/api/knowledge/list")
def api_knowledge_list(request: Request, scope: dict = Depends(Scope())):
    """Qaydlar ro'yxati (`?category=Xatolar` bilan filtrlanadi)."""
    _require_super(scope)
    category = (request.query_params.get("category") or "").strip()
    return vault.list_notes(category)


@router.get("/api/knowledge/search")
def api_knowledge_search(request: Request, scope: dict = Depends(Scope())):
    """Matn bo'yicha qidirish (`?q=...`)."""
    _require_super(scope)
    q = (request.query_params.get("q") or "").strip()
    try:
        limit = int(request.query_params.get("limit") or 20)
    except ValueError:
        limit = 20
    return vault.search_notes(q, limit=limit)


@router.get("/api/knowledge/graph")
def api_knowledge_graph(scope: dict = Depends(Scope())):
    """Obsidian grafigi — qaydlar va `[[havolalar]]` (tugun/qirra) + holat."""
    _require_super(scope)
    data = vault.graph()
    data["status"] = vault.graph_status(data)
    return data


@router.get("/api/knowledge/note")
def api_knowledge_note(request: Request, scope: dict = Depends(Scope())):
    """Bitta qaydni o'qish (`?path=Qoidalar/Asosiy-invariantlar.md`)."""
    _require_super(scope)
    path = (request.query_params.get("path") or "").strip()
    try:
        return vault.read_note(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/knowledge/note")
async def api_knowledge_save(request: Request, scope: dict = Depends(Scope())):
    """Yangi qayd yozadi yoki mavjudini almashtiradi.

    Payload: `{title, category, content, overwrite?, path?}`
    `path` berilsa va `overwrite` bo'lsa — o'sha fayl yangilanadi.
    """
    _require_super(scope)
    payload = await request.json()
    title = str(payload.get("title") or "").strip()
    category = str(payload.get("category") or "").strip()
    content = str(payload.get("content") or "")
    overwrite = bool(payload.get("overwrite"))
    path = str(payload.get("path") or "").strip()

    try:
        res = vault.write_note(title=title, category=category, content=content,
                               overwrite=overwrite, path=path if overwrite else "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if res.get("ok"):
        log.info("Panel: bilim bazasiga qayd yozildi (%s)", res.get("path"))
    return res


@router.post("/api/knowledge/delete")
async def api_knowledge_delete(request: Request, scope: dict = Depends(Scope())):
    """Qaydni o'chiradi. Payload: `{path}` (faqat `.md`, vault ichida)."""
    _require_super(scope)
    payload = await request.json()
    path = str(payload.get("path") or "").strip()
    try:
        res = vault.delete_note(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if res.get("ok"):
        log.info("Panel: bilim bazasidan qayd o'chirildi (%s)", res.get("path"))
    return res
