"""Xujjat shablonlari — tayyor namunalar va AI uchun promptlar.

Har bir shablon:
    - key:     API kaliti (URL va DB uchun)
    - name:    Foydalanuvchiga ko'rinadigan nom
    - category: Guruh (mehnat, ma'muriy, aloqa)
    - fields:  To'ldirilishi kerak bo'lgan maydonlar
    - body:    HTML/Text shablon ({{field}} placeholderlar bilan)
    - ai_prompt: AI uchun prompt (shablonni to'ldirish uchun)
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any


@dataclass
class TemplateField:
    """Shablon maydoni."""
    key: str
    label: str
    type: str = "text"  # text, date, number, select, textarea
    required: bool = False
    placeholder: str = ""
    options: list[str] = dc_field(default_factory=list)
    default: str = ""


@dataclass
class DocumentTemplate:
    """Xujjat shabloni."""
    key: str
    name: str
    category: str
    description: str
    fields: list[TemplateField]
    body: str
    ai_prompt: str = ""
    file_ext: str = "txt"


# ---------------------------------------------------------------------------
# MEHNAT SHARTNOMASI
# ---------------------------------------------------------------------------
MEHNAT_SHARTNOMASI = DocumentTemplate(
    key="mehnat_shartnomasi",
    name="Mehnat Shartnomasi",
    category="mehnat",
    description="Mehnat munosabatlarini rasmiylashtirish uchun shartnoma",
    fields=[
        TemplateField("tashkilot_nomi", "Tashkilot nomi", required=True,
                      placeholder="Masalan: BM Trans LLC"),
        TemplateField("tashkilot_stir", "Tashkilot STIR", required=True,
                      placeholder="123456789"),
        TemplateField("tashkilot_manzili", "Tashkilot manzili",
                      placeholder="Toshkent sh., ..."),
        TemplateField("haydovchi_fio", "Haydovchi F.I.O.", required=True),
        TemplateField("haydovchi_tin", "Haydovchi JSHSHIR", required=True),
        TemplateField("haydovchi_pasport", "Pasport ma'lumotlari",
                      placeholder="AB1234567"),
        TemplateField("lavozimi", "Lavozimi", default="Haydovchi",
                      options=["Haydovchi", "Mexanik", "Dispetcher", "Boshqa"]),
        TemplateField("oyoq_ishi", "Oyoq ishi (km/oy)", type="number",
                      placeholder="2500"),
        TemplateField("oylik_olishi", "Oylik maosh (so'm)", type="number",
                      placeholder="5000000"),
        TemplateField("ish_boshlash_sana", "Ish boshlash sanasi", type="date",
                      required=True),
        TemplateField("shartnoma_muddati", "Shartnoma muddati",
                      options=["Muddatli (1 yil)", "Muddatsiz"]),
        TemplateField("haydovchi_uchi", "Haydovchi uchun shartlar",
                      type="textarea", placeholder="Qo'shimcha shartlar..."),
    ],
    body="""Mehnat Shartnomasi

Toshkent shahri, {{ish_boshlash_sana}} sanasida quyidagilar o'rtasida tuzildi:

1. {{tashkilot_nomi}} (STIR: {{tashkilot_stir}}, manzil: {{tashkilot_manzili}}), bundan keyin "Ish beruvchi" deb ataladi, bir tomondan;

2. {{haydovchi_fio}} (JSHSHIR: {{haydovchi_tin}}, pasport: {{haydovchi_pasport}}), bundan keyin "Xodim" deb ataladi, ikkinchi tomondan.

BU SHARTNOMANING SHARTLARI:

1. Xodim {{tashkilot_nomi}} tashkilotida "{{lavozimi}}" lavozimida ishga qabul qilinadi.

2. Ish boshlash sanasi: {{ish_boshlash_sana}}.

3. Ish vaqti: haftasiga 40 soat (O'zbekiston Respublikasi Mehnat kodeksi 49-moddasi).

4. Oylik maosh: {{oylik_olishi}} so'm (sof). Oylik maosh har oyning 5-sanasigacha to'lanadi.

5. Oyoq ishi: oyiga {{oyoq_ishi}} km.

6. Shartnoma muddati: {{shartnoma_muddati}}.

7. Xodim quyidagi majburiyatlarni oladi:
   - Yo'nalish bo'yicha regular yo'nalish xizmatini ko'rsatish
   - Transport vositasinitexnik holatini kuzatib borish
   - Yo'lovchilarga madaniyat bilan xizmat ko'rsatish
   - Yo'l qoidalariga rioya qilish

8. Qo'shimcha shartlar: {{haydovchi_uchi}}

ISH BERUVCHI                          XODIM
_______________                       _______________
(Muhr)                                (Imzo)

""")
# ---------------------------------------------------------------------------
# DALOLATNOMA
# ---------------------------------------------------------------------------
DALOLATNOMA = DocumentTemplate(
    key="dalolatnoma",
    name="Dalolatnoma",
    category="ma'muriy",
    description="Ish faoliyatini tasdiqlovchi dalolatnoma",
    fields=[
        TemplateField("tashkilot_nomi", "Tashkilot nomi", required=True),
        TemplateField("dalolatnoma_raqami", "Dalolatnoma raqami", required=True),
        TemplateField("dalolatnoma_sana", "Sanasi", type="date", required=True),
        TemplateField("haydovchi_fio", "Haydovchi F.I.O.", required=True),
        TemplateField("lavozimi", "Lavozimi", default="Haydovchi"),
        TemplateField("ish_tajribasi", "Umumiy ish tajribasi (yil)", type="number"),
        TemplateField("yo_nishoni", "Yo'nalish nomi"),
        TemplateField("tashqi_sana", "Tashqi sana", type="date"),
        TemplateField("maqsad", "Dalolatnoma maqsadi", type="textarea",
                      placeholder="Qaysi maqsadda berilmoqda..."),
    ],
    body="""DALOLATNOMA

{{tashkilot_nomi}}                    № {{dalolatnoma_raqami}}
                                            "{{dalolatnoma_sana}}"

Biz, {{tashkilot_nomi}} tashkiloti,
quyidagini DALOLATGA OLAMIZ:

{{haydovchi_fio}}, {{lavozimi}} lavozimida,
{{ish_tajribasi}} yil ish tajribasiga ega bo'lib,
{{yo_nishoni}} yo'nalishi bo'yicha xizmat ko'rsatmoqda.

Ushbu dalolatnoma {{maqsad}} uchun berildi.

Bu dalolatnoma boshqa tashkilotlarga taqdim etilishi mumkin.

Tashkilot rahbari:
_______________
(Muhr, imzo)
""")

# ---------------------------------------------------------------------------
# BUYRUQ
# ---------------------------------------------------------------------------
BUYRUQ = DocumentTemplate(
    key="buyruq",
    name="Buyruq (Farmoyish)",
    category="ma'muriy",
    description="Tashkilot buyruqlari va farmoyishlari",
    fields=[
        TemplateField("tashkilot_nomi", "Tashkilot nomi", required=True),
        TemplateField("buyruq_raqami", "Buyruq raqami", required=True),
        TemplateField("buyruq_sana", "Buyruq sanasi", type="date", required=True),
        TemplateField("buyruq_mavzusi", "Mavzusi", required=True,
                      placeholder="Haydovchini ishga qabul qilish"),
        TemplateField("buyruq_matni", "Buyruq matni", type="textarea", required=True),
        TemplateField("asos", "Asos ( buyruq asosida )", type="textarea"),
        TemplateField("masul_shaxs", "Mas'ul shaxs F.I.O."),
    ],
    body="""{{tashkilot_nomi}}
B U Y R U Q
{{buyruq_sana}}               № {{buyruq_raqami}}

"{{buyruq_mavzusi}}"

{{buyruq_matni}}

Asos: {{asos}}

Ijro etish uchun mas'ul: {{masul_shaxs}}

Boshqaruv rahbari:
_______________
(Muhr, imzo)
""")

# ---------------------------------------------------------------------------
# ALOQA XATI
# ---------------------------------------------------------------------------
ALOQA_XATI = DocumentTemplate(
    key="alokaxati",
    name="Aloqa Xati",
    category="aloha",
    description="Tashqi va ichki aloqa xatlari",
    fields=[
        TemplateField("tashkilot_nomi", "Yuboruvchi tashkilot", required=True),
        TemplateField("tashkilot_manzili", "Manzil"),
        TemplateField("tashkilot_telefon", "Telefon"),
        TemplateField("qabul_qiluvchi", "Qabul qiluvchi", required=True),
        TemplateField("qabul_manzil", "Qabul qiluvchi manzili"),
        TemplateField("xat_raqami", "Xat raqami", required=True),
        TemplateField("xat_sana", "Sanasi", type="date", required=True),
        TemplateField("xat_mavzusi", "Mavzusi", required=True),
        TemplateField("xat_matni", "Xat matni", type="textarea", required=True,
                      placeholder="Xat matnini kiriting..."),
        TemplateField("ilova", "Ilovalar"),
        TemplateField("imzo_olgan", "Imzo olgan shaxs"),
    ],
    body="""{{tashkilot_nomi}}
{{tashkilot_manzili}}
Tel: {{tashkilot_telefon}}

                                {{qabul_qiluvchi}}
                                {{qabul_manzil}}

                          № {{xat_raqami}}
                          "{{xat_sana}}"

                               {{xat_mavzusi}}

Hurmatli {{qabul_qiluvchi}}!

{{xat_matni}}

Ilovalar: {{ilova}}

Hurmat bilan,
{{imzo_olgan}}
{{tashkilot_nomi}}
""")

# ---------------------------------------------------------------------------
# TILXAT (ARIZA)
# ---------------------------------------------------------------------------
TILXAT = DocumentTemplate(
    key="tilxat",
    name="Tilxat (Ariza)",
    category="aloha",
    description="Ishga qabul qilish, ta'til olish va boshqa arizalar",
    fields=[
        TemplateField("tashkilot_nomi", "Tashkilot nomi", required=True),
        TemplateField("rahbar_fio", "Rahbar F.I.O.", required=True),
        TemplateField("ariza_sana", "Ariza sanasi", type="date", required=True),
        TemplateField("ariza_turi", "Ariza turi", required=True, options=[
            "Ishga qabul qilish",
            "Ta'til olish",
            "Ishdan bo'shatish",
            "Maosh oshirish",
            "Komissiya so'rash",
            "Boshqa",
        ]),
        TemplateField("ariza_matni", "Ariza matni", type="textarea", required=True,
                      placeholder="Ariza matnini kiriting..."),
        TemplateField("fio", "Ariza muallifi F.I.O.", required=True),
        TemplateField("lavozimi", "Lavozimi"),
        TemplateField("telefon", "Telefon"),
    ],
    body="""                    {{tashkilot_nomi}} rahbari
                    {{rahbar_fio}} ga

                         ________ dan
                         {{lavozimi}}
                         {{fio}}

                             T I L X A T

                                 "{{ariza_sana}}"

{{ariza_matni}}

Ilovalar: mavjud bo'lsa ko'rsatiladi.

Telefon: {{telefon}}

                            {{fio}} {{ariza_sana}} y.
                            _______________
                            (Imzo)
""")


# ---------------------------------------------------------------------------
# TEMPLATE REGISTRY
# ---------------------------------------------------------------------------
TEMPLATES: dict[str, DocumentTemplate] = {
    t.key: t for t in [
        MEHNAT_SHARTNOMASI,
        DALOLATNOMA,
        BUYRUQ,
        ALOQA_XATI,
        TILXAT,
    ]
}

CATEGORIES: dict[str, str] = {
    "mehnat": "Mehnat munosabatlari",
    "ma'muriy": "Ma'muriy hujjatlar",
    "aloha": "Aloqa hujjatlari",
}


def get_template(key: str) -> DocumentTemplate | None:
    return TEMPLATES.get(key)


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            "key": t.key,
            "name": t.name,
            "category": t.category,
            "description": t.description,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "type": f.type,
                    "required": f.required,
                    "placeholder": f.placeholder,
                    "options": f.options,
                    "default": f.default,
                }
                for f in t.fields
            ],
        }
        for t in TEMPLATES.values()
    ]
