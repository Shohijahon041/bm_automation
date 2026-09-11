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
