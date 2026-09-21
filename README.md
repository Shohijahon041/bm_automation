# Brutto-shartnoma to'lov hisoblagichi (116-son qaror)

Jamoat avtobus yo'nalishlari bo'yicha **Vazirlar Mahkamasining 2023-yil 18-martdagi
116-son qarori** (1-ilova, 32–33-bandlar) asosida tashuvchiga to'lanadigan
summani hisoblaydigan Python moduli.

## O'rnatish

Talab qilinadigan kutubxonalar:

```bash
pip install pandas openpyxl pytest
```

Python 3.10+ kerak.

## Asosiy formula (32-band)

```
S = SKM × Lf × (1 − α) × (1 − β) × (1 − γ)
```

| Belgisi | Ma'nosi |
|---|---|
| `S` | tashuvchiga to'lanadigan summa (so'm) |
| `SKM` | 1 mashina-km narxi (masalan, 16176 so'm) |
| `Lf` | amalda bosib o'tilgan masofa (km) |
| `α` | "bosib o'tilgan yo'l" bo'yicha ushlab qolinish |
| `β` | sifat mezoni bo'yicha ushlab qolinish |
| `γ` | majburiyatlarga amal qilish bo'yicha ushlab qolinish |

Koeffitsiyentlar indeks bo'yicha aniqlanadi (masalan, hajm indeksi
`Lf/Lr×100%` 95–100% bo'lsa `α=0`, 0–69.9% bo'lsa `α=0.20` va hokazo).

To'lov muddati (33-band): **hisobot oyidan keyingi oyning 15-sanasiga qadar.**

## Foydalanish

```python
from datetime import date
from brutto_calculator import Contract, BruttoCalculator, load_excel

contract = Contract(skm=16176.0, route_number="B-80", carrier="AvtoPark",
                    valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31))
calc = BruttoCalculator(contract)

df = load_excel("source_brutto.xlsx")
results = calc.process_report(df)
jami = calc.aggregate(results)
calc.export_excel(results, "hisob_brutto.xlsx")   # JAMI qatori bilan
```

To'liq namuna: `python example.py`.

## Manba fayl ustunlari

| Ustun | Tavsif |
|---|---|
| `sana` | qatnov sanasi |
| `grafik` | grafik/smena |
| `davlat_raqami` | avtobus davlat raqami |
| `fio` | haydovchi F.I.O |
| `lr` | reja km |
| `lf` | amalda km |
| `kamal` | amalda bajarilgan qatnovlar |
| `kstjb` | sifat talabiga javob bermagan qatnovlar |
| `kmaq` | majburiyatiga amal qilmagan qatnovlar |

## Alohida holatlar

- `Lr = 0` yoki `Kamal = 0` bo'lsa eng yuqori jazo koeffitsiyenti qo'llaniladi
  (`α=0.20`, `β=0.15`, `γ=0.05`).
- Bajarilmagan qatnov (YTH, tirbandlik, GPS nosozlik) to'lovda hisobga olinadi.

## Testlar

```bash
python -m pytest test_brutto_calculator.py -v
```