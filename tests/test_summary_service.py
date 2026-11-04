"""Oylik yig'ma xizmati: monthly_rows / driver_summary testlari."""

from bm_automation.app.services.summary_service import driver_summary, monthly_rows


def _days():
    return [
        {
            "date": "2026-08-10",
            "duty": {
                "graphs": [
                    {
                        "graphName": "P1",
                        "driverName": "Aliyev Aliy",
                        "startTime": "06:00:00",
                        "endTime": "14:00:00",
                        "plateNum": "01A123AA",
                        "shiftName": "DU",
                        "hasSecond": False,
                    }
                ]
            },
        },
        {
            "date": "2026-08-11",
            "duty": {
                "graphs": [
                    {
                        "graphName": "P1",
                        "driverName": "Aliyev Aliy",
                        "startTime": "06:00:00",
                        "endTime": "14:00:00",
                        "plateNum": "01A123AA",
                        "shiftName": "DU",
                        "hasSecond": True,
                        "secondDriverName": "Karimov Karim",
                        "secondStartTime": "14:00:00",
                        "secondEndTime": "22:00:00",
                    }
                ]
            },
        },
        {"date": "2026-08-12", "duty": None},
    ]


def test_monthly_rows_structure():
    rows, graphs = monthly_rows(_days())
    assert graphs == ["P1"]
    assert len(rows) == 3
    assert rows[0]["date"] == "2026-08-10"
    assert rows[0]["graphs"]["P1"]["driver"] == "Aliyev Aliy"
    # ikkinchi haydovchi qatorga qo'shiladi
    assert rows[1]["graphs"]["P1"]["driver"] == "Aliyev Aliy / Karimov Karim"
    # duty bo'lmagan kun bo'sh
    assert rows[2]["graphs"]["P1"]["driver"] == ""


def test_driver_summary_counts_days_and_hours():
    rows = driver_summary(_days())
    by_name = {r["driver"]: r for r in rows}
    assert by_name["Aliyev Aliy"]["days"] == 2
    assert by_name["Aliyev Aliy"]["hours"] == 16.0
    assert by_name["Karimov Karim"]["days"] == 1
    assert by_name["Karimov Karim"]["hours"] == 8.0


def test_driver_summary_empty():
    assert driver_summary([]) == []


def test_shift_hours_crosses_midnight():
    """Yarim tuni kesib o'tuvchi smena (22:00–06:00) manfiy chiqmasligi kerak."""
    from bm_automation.app.services.summary_service import _shift_hours

    assert _shift_hours("22:00:00", "06:00:00") == 8.0
    assert _shift_hours("06:00", "14:00") == 8.0
    assert _shift_hours("", "06:00:00") == 0.0
    assert _shift_hours("not-a-time", "06:00:00") == 0.0


def test_driver_summary_overnight_shift_hours():
    days = [{"date": "2026-08-15", "duty": {"graphs": [
        {"graphName": "P2", "driverName": "Tun Osirovi",
         "startTime": "22:00:00", "endTime": "06:00:00",
         "plateNum": "01B456BB", "shiftName": "TUN", "hasSecond": False},
    ]}}]
    rows = driver_summary(days)
    assert rows[0]["hours"] == 8.0
