"""Duty service: graphs -> qatorlar (2-haydovchi ham) testlari."""

from bm_automation.app.services.duty_service import graphs_to_rows


def _duty(has_second=True):
    return {
        "id": "d1",
        "date": "2026-08-10",
        "graphs": [
            {
                "graphName": "P1",
                "shiftName": "DU",
                "driverName": "Aliyev Aliy",
                "timeTableNumber": "101",
                "plateNum": "01A123AA",
                "garageNumber": "G1",
                "vehicleModel": "Mercedes",
                "vehicleId": "v1",
                "startTime": "06:00:00",
                "endTime": "14:00:00",
                "tripCount": 5,
                "isReplaced": False,
                "driverId": "drv1",
                "hasSecond": has_second,
                "secondDriverName": "Karimov Karim",
                "secondStartTime": "14:00:00",
                "secondEndTime": "22:00:00",
                "secondDriverId": "drv2",
            }
        ],
    }


def test_graphs_to_rows_single_driver():
    rows = graphs_to_rows(_duty(has_second=False))
    assert len(rows) == 1
    assert rows[0]["driverName"] == "Aliyev Aliy"


def test_graphs_to_rows_second_driver_preserved():
    rows = graphs_to_rows(_duty(has_second=True))
    assert len(rows) == 2
    assert rows[0]["driverName"] == "Aliyev Aliy"
    assert rows[0]["driverId"] == "drv1"
    assert rows[1]["driverName"] == "Karimov Karim"
    assert rows[1]["driverId"] == "drv2"
    assert rows[1]["startTime"] == "14:00:00"
    assert rows[1]["endTime"] == "22:00:00"
    # ikkinchi qator birinchi ustunlarini meros qilmaydi
    assert rows[1]["graphName"] == "P1"
    assert rows[1]["plateNum"] == "01A123AA"


def test_graphs_to_rows_empty():
    assert graphs_to_rows({"date": "2026-08-10", "graphs": []}) == []
    assert graphs_to_rows({}) == []
