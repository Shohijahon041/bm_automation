"""Xujjat yaratish dvigati — shablonni to'ldirish va AI yordamida generatsiya.

Xususiyatlar:
    1. Shablonni maydonlar bilan to'ldirish (render)
    2. AI (suniy intellekt) yordamida xujjat matnini yaratish
    3. Xujjatni PDF/DOCX formatiga o'tkazish (kelajakda)
    4. Tayyor xujjatlarni saqlash va boshqarish
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..utils.logger import get_logger
from .templates import DocumentTemplate, TEMPLATES, get_template

log = get_logger("bm_automation.documents")


def render_template(template: DocumentTemplate, fields: dict[str, str]) -> str:
    """Shablonni maydonlar bilan to'ldiradi.

    {{field}} placeholderlarini haqiqiy qiymatlar bilan almashtiradi.
    Agar maydon kiritilmagan bo'lsa — bo'sh qoldiradi.
    """
    text = template.body
    for f in template.fields:
        value = fields.get(f.key, f.default or "")
        placeholder = "{{" + f.key + "}}"
        text = text.replace(placeholder, str(value))
    return text


def render_html(template: DocumentTemplate, fields: dict[str, str]) -> str:
    """Xujjatni HTML formatida qaytaradi (chop etish uchun)."""
    text = render_template(template, fields)
    # Oddiy matnni HTML ga aylantirish
    html = text.replace("\n", "<br>\n")
    return f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="utf-8">
<title>{template.name}</title>
<style>
  body {{
    font-family: "Times New Roman", serif;
    font-size: 14px;
    line-height: 1.8;
    max-width: 800px;
    margin: 40px auto;
    padding: 40px;
    color: #1a1a1a;
    background: white;
  }}
  h1, h2, h3 {{
    text-align: center;
    font-weight: bold;
    margin: 20px 0;
  }}
  .header {{
    text-align: center;
    margin-bottom: 30px;
    border-bottom: 2px solid #333;
    padding-bottom: 15px;
  }}
  .field-value {{
    border-bottom: 1px dotted #666;
    display: inline-block;
    min-width: 120px;
    padding: 0 4px;
  }}
  .footer {{
    margin-top: 40px;
    display: flex;
    justify-content: space-between;
  }}
  .signature-block {{
    text-align: center;
    min-width: 200px;
  }}
  @media print {{
    body {{ margin: 0; padding: 20px; }}
  }}
</style>
</head>
<body>
{html}
</body>
</html>"""


def generate_ai_content(template: DocumentTemplate, context: dict[str, str],
                        custom_prompt: str = "") -> dict[str, str]:
    """AI yordamida xujjat maydonlarini avtomatik to'ldiradi.

    Bu funksiya oddiy AI prompt yaratadi. Haqiqiy AI integratsiyasi
    uchun tashqi API (OpenAI, Anthropic, lokal model) ishlatilishi kerak.

    Hozircha: shablon promptini tayyorlab, foydalanuvchiga ko'rsatadi.
    """
    # AI uchun kontekst yaratish
    context_parts = []
    for key, value in context.items():
        if value:
            context_parts.append(f"- {key}: {value}")

    context_text = "\n".join(context_parts) if context_parts else "Ma'lumotlar yo'q"

    # Shablon AI promptini tayyorlash
    base_prompt = template.ai_prompt or (
        f"Quyidagi ma'lumotlar asosida \"{template.name}\" xujjatini tuzing.\n"
        f"Xujjat rasmiy tilida, O'zbek tilida yozilishi kerak.\n\n"
        f"Ma'lumotlar:\n{context_text}\n\n"
        f"Maydonlar: {', '.join(f.label for f in template.fields)}"
    )

    if custom_prompt:
        base_prompt = f"{custom_prompt}\n\n{base_prompt}"

    return {
        "prompt": base_prompt,
        "context": context_text,
        "template": template.name,
        "note": "AI integratsiyasi hozircha tayyorlanmoqda. "
                "Ma'lumotlarni qo'lda to'ldiring yoki AI API ulang.",
    }


def validate_fields(template: DocumentTemplate, fields: dict[str, str]) -> list[str]:
    """Maydonlarni tekshiradi. Xatoliklar ro'yxatini qaytaradi."""
    errors = []
    for f in template.fields:
        if f.required and not fields.get(f.key, "").strip():
            errors.append(f"'{f.label}' maydoni to'ldirilishi shart")
    return errors


def create_document(template_key: str, fields: dict[str, str],
                    title: str = "", status: str = "draft",
                    attachments: list[str] | None = None) -> dict[str, Any]:
    """Yangi xujjat yaratadi va metadatani qaytaradi."""
    template = get_template(template_key)
    if not template:
        return {"error": f"Shablon topilmadi: {template_key}"}

    errors = validate_fields(template, fields)
    if errors:
        return {"error": "Validatsiya xatolari", "errors": errors}

    body_text = render_template(template, fields)
    body_html = render_html(template, fields)

    doc = {
        "title": title or template.name,
        "template_key": template_key,
        "fields": fields,
        "body_text": body_text,
        "body_html": body_html,
        "status": status,
        "attachments": attachments or [],
    }
    return doc


def list_template_categories() -> dict[str, str]:
    """Mavjud shablon guruhlarini qaytaradi."""
    from .templates import CATEGORIES
    return dict(CATEGORIES)


# ---------------------------------------------------------------------------
# AVTO-TO'LDIRISH (haydovchi + kompaniya ma'lumotlari)
# ---------------------------------------------------------------------------
# Shablon maydoni -> ma'lumot manbai kaliti. Manba driver_autofill_context()
# lug'atidagi kalitlardan biri: full_name, tin, phone, route_name,
# passport_number, today, company_name, company_stir, company_address,
# company_phone, position va boshlar.
_FIELD_SOURCE: dict[str, str] = {
    # haydovchiga bog'liq
    "haydovchi_fio": "full_name",
    "fio": "full_name",
    "haydovchi_tin": "tin",
    "haydovchi_pasport": "passport_number",
    "telefon": "phone",
    "yo_nishoni": "route_name",
    "lavozimi": "position",
    # sana maydonlari -> bugun
    "ish_boshlash_sana": "today",
    "buyruq_sana": "today",
    "xat_sana": "today",
    "ariza_sana": "today",
    "dalolatnoma_sana": "today",
    "tashqi_sana": "today",
    # kompaniya (tashkilot)
    "tashkilot_nomi": "company_name",
    "tashkilot_stir": "company_stir",
    "tashkilot_manzili": "company_address",
    "tashkilot_telefon": "company_phone",
}


def company_context() -> dict:
    """Kompaniya (tashkilot) nomi va rekvizitlari.

    Nomi — faol profil (`profiles.json`` active), STIR/manzil/telefon —
    ixtiyoriy `.env` kalitlari: ``COMPANY_STIR``, ``COMPANY_ADDRESS``,
    ``COMPANY_PHONE``. Ko'rsatilmagani bo'sh qoladi.
    """
    import os

    from ..core.profiles import get_profile

    name = ""
    try:
        name = str((get_profile() or {}).get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    return {
        "name": name,
        "stir": os.getenv("COMPANY_STIR", "").strip(),
        "address": os.getenv("COMPANY_ADDRESS", "").strip(),
        "phone": os.getenv("COMPANY_PHONE", "").strip(),
    }


def _company_for_route(route_id: str) -> str:
    """Yo'nalish ID bo'yicha profil nomi (kompaniya)."""
    try:
        from ..core.profiles import all_profiles
        for p in all_profiles():
            if str(p.get("routeVariantId") or "").strip() == route_id:
                return str(p.get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def driver_autofill_context(driver_id: str = "") -> dict:
    """Haydovchi (va kompaniya) ma'lumotlarini yig'adi.

    Hujjat maydonlarini avto-to'ldirish uchun yagona manba. ``driver_id``
    bo'sh bo'lsa — faqat kompaniya + bugungi sana qaytadi (haydovchi
    bo'limlari bo'sh), dialog ochilganda bazani to'ldirish uchun ishlatiladi.
    """
    from datetime import date

    from ..db.storage import get_storage

    did = str(driver_id or "").strip()
    st = get_storage()
    profile = st.find("driver_profiles", driver_id=did) or {}
    driver = st.find("drivers", external_id=did) or {}
    route_id = str(driver.get("route_id") or "")
    route_name = ""
    company = company_context()
    if route_id:
        route = st.find("routes", external_id=route_id)
        route_name = str(route.get("name") or "") if route else ""
        own = _company_for_route(route_id)
        if own:
            company["name"] = own
    today = date.today().isoformat()
    return {
        "driver_id": did,
        "full_name": str(driver.get("full_name") or ""),
        "tin": str(driver.get("tin") or ""),
        "route_id": route_id,
        "route_name": route_name,
        "phone": str(profile.get("phone") or ""),
        "passport_number": str(profile.get("passport_number") or ""),
        "passport_issued_by": str(profile.get("passport_issued_by") or ""),
        "passport_expiry": str(profile.get("passport_expiry") or ""),
        "license_number": str(profile.get("license_number") or ""),
        "license_category": str(profile.get("license_category") or ""),
        "license_expiry": str(profile.get("license_expiry") or ""),
        "today": today,
        "position": "Haydovchi",
        "company_name": company.get("name") or "",
        "company_stir": company.get("stir") or "",
        "company_address": company.get("address") or "",
        "company_phone": company.get("phone") or "",
    }


def autofill_field_values(template_key: str = "",
                          context: dict | None = None) -> dict[str, str]:
    """Shablon maydonlari uchun avto-qiymatlar xaritasi.

    ``template_key`` bo'sh bo'lsa barcha shablon maydonlari hisobga olinadi.
    Manbadan qiymat bo'lmagan va mapping bo'lmagan maydonlarning ``default``
    qiymati ishlatiladi; bo'sh qiymatlar chiqarilmaydi.
    """
    context = context or {}
    from .templates import TEMPLATES, get_template

    tpls = ([get_template(template_key)] if template_key
            else list(TEMPLATES.values()))
    out: dict[str, str] = {}
    for t in tpls:
        if not t:
            continue
        for f in t.fields:
            val = ""
            src = _FIELD_SOURCE.get(f.key)
            if src and context.get(src) not in (None, ""):
                val = str(context.get(src, ""))
            elif f.default:
                val = str(f.default)
            if not val:
                continue
            if f.type == "select" and f.options and val not in f.options:
                continue
            out[f.key] = val
    return out
