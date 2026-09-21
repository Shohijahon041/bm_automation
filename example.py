"""brutto_calculator ishlatish namunasi (example).

B-80 yo'nalishi misolida Excel manba fayl yaratiladi, brutto-shartnoma
bo'yicha to'lov hisoblanadi va natija Excel'ga eksport qilinadi.

Ishga tushirish:
    python example.py
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from brutto_calculator import (
    BruttoCalculator,
    Contract,
    load_excel,
    payment_due_date,
)

SOURCE_XLSX = "source_brutto.xlsx"
OUTPUT_XLSX = "hisob_brutto.xlsx"


def main() -> None:
    """Namunaviy hisob-kitobni bajaradi."""
    # 1) Shartnoma (116-son qarori asosida 1 km narx = 16176 so'm)
    contract = Contract(
        skm=16176.0,
        route_number="B-80",
        carrier="MChJ 'Shahar AvtoPark'",
        valid_from=date(2024, 1, 1),
        valid_to=date(2024, 12, 31),
    )
    calc = BruttoCalculator(contract)

    # 2) Manba (kirish) DataFrame: hisobot oyi reyslari
    source = pd.DataFrame([
        {
            "sana": "2024-06-10",
            "grafik": "1",
            "davlat_raqami": "01A123BC",
            "fio": "Karimov Anvar",
            "lr": 120.0,   # reja km
            "lf": 110.0,   # amalda km
            "kamal": 100,  # amalda bajarilgan qatnovlar
            "kstjb": 2,    # sifat talabiga javob bermagan
            "kmaq": 1,     # majburiyat buzilgan qatnovlar
        },
        {
            "sana": "2024-06-11",
            "grafik": "2",
            "davlat_raqami": "01A124BC",
            "fio": "Aliyev Botir",
            "lr": 120.0,
            "lf": 95.0,
            "kamal": 100,
            "kstjb": 10,
            "kmaq": 5,
        },
        {
            "sana": "2024-06-12",
            "grafik": "1",
            "davlat_raqami": "01A123BC",
            "fio": "Karimov Anvar",
            "lr": 120.0,
            "lf": 100.0,
            "kamal": 100,
            "kstjb": 0,
            "kmaq": 0,
        },
    ])

    # 3) Excel manba faylni yozish (yoki mavjud fayldan o'qish)
    source.to_excel(SOURCE_XLSX, index=False, sheet_name="Hisobot")
    loaded = load_excel(SOURCE_XLSX, sheet="Hisobot")
    print(f"Manba fayl: {SOURCE_XLSX} ({len(loaded)} qator)")

    # 4) Hisob-kitob
    results = calc.process_report(loaded)
    agg = calc.aggregate(results)

    # 5) Hisobotni konsolga chiqarish
    print("\n=== HISOB-KITOB (116-son, 32-band) ===")
    for r in results:
        print(
            f"{r.sana} | {r.davlat_raqami} | {r.fio}"
            f" | Fa= {r.hajm_index:.1f}% / Sf= {r.sifat_index:.1f}%"
            f" / Mb= {r.majb_index:.1f}%"
            f" | a={r.alpha:.4f} b={r.beta:.4f} g={r.gamma:.4f}"
            f" | To'lov: {r.tolov:,.0f} so'm".replace(",", " ")
        )
    print(
        f"\nUMUMIY TO'LOV: {agg['tolov']:,.0f} so'm"
        .replace(",", " ")
    )

    # 6) Natijani Excel'ga eksport qilish (JAMI qatori bilan)
    calc.export_excel(results, OUTPUT_XLSX)
    print(f"Natija fayl: {OUTPUT_XLSX}")

    # 7) To'lov muddati (33-band): hisobot oyidan keyingi oyning 15-sanasi
    due = payment_due_date(date(2024, 6, 10))
    print(f"To'lov muddati: {due} (33-bandga muvofiq)")
    print(f"Shartnoma amal qiladimi: {contract.is_active_on(due)}")


if __name__ == "__main__":
    main()