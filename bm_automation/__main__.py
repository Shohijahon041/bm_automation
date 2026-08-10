"""BM avtomatizatsiya CLI.

Ishlatish:
    python -m bm_automation login
    python -m bm_automation login-browser
    python -m bm_automation routes
    python -m bm_automation gross-trip [--route ID] [--date YYYY-MM-DD] [--out reports]
    python -m bm_automation gross-route [--route ID] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--out reports]
    python -m bm_automation waybill [--route ID] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--status APPROVED]
    python -m bm_automation schedule --route ID --time 22:00 [--notify] [--offset 1]
    python -m bm_automation duty [--route ID] [--date YYYY-MM-DD] [--out reports]
    python -m bm_automation drivers --profile NAME [--send] [--date YYYY-MM-DD] [--offset 0|1]
    python -m bm_automation drivers --all [--send] [--offset 1]
    python -m bm_automation daily [--offset 1] [--no-send]
    python -m bm_automation profiles list
    python -m bm_automation profiles add "Kompanya" --route ID --start1 "Prez Oldi" --start2 "Oybek Massiv"
    python -m bm_automation login-browser --visible   # profillar va ID'larini aniqlash
    python -m bm_automation notify-test --text "Salom"
    python -m bm_automation reports [--name bus-region] [--type DAILY] [--date 2026-08-01] [--out reports]
    python -m bm_automation list-reports
"""

from __future__ import annotations

import sys

from .cli.parser import main

if __name__ == "__main__":
    sys.exit(main())
