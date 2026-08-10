"""Repositories qatlami — API'ga ma'lumot kirish (data access).

Har bir repository faqat bitta BM API sohasi (duty, shift, haydovchi,
avtobus, waybill, gross, route) bilan ishlaydi. Business logic shu yerga
KIRMAYDI — services qatlamida joylashadi.
"""

from .duty_repo import DutyRepository  # noqa: F401
from .driver_repo import DriverRepository  # noqa: F401
from .gross_repo import GrossRepository  # noqa: F401
from .route_repo import RouteRepository  # noqa: F401
from .shift_repo import ShiftRepository  # noqa: F401
from .vehicle_repo import VehicleRepository  # noqa: F401
from .waybill_repo import WaybillRepository  # noqa: F401

__all__ = [
    "DutyRepository",
    "DriverRepository",
    "GrossRepository",
    "RouteRepository",
    "ShiftRepository",
    "VehicleRepository",
    "WaybillRepository",
]
