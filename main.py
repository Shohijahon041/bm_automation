"""Asosiy ishga tushirish nuqtasi.

    python main.py <buyruq>

BM Avtomatizatsiya (bm.dtransport.uz) CLI'ni ishga tushiradi.
To'liq variant: `python -m bm_automation <buyruq>`.
"""

import sys

from bm_automation.cli.parser import main

if __name__ == "__main__":
    sys.exit(main())
