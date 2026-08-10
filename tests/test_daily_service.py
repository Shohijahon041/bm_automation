"""Kunlik vazifa mantiqiy tekshiruvlari."""

from bm_automation.app.services.daily_service import _auth_error, _has_failures


def test_has_failures_empty_is_failure():
    assert _has_failures([]) is True


def test_has_failures_all_sent():
    assert _has_failures([{"sent": True, "monthly_sent": True}]) is False


def test_has_failures_on_error():
    assert _has_failures([{"error": "HTTP 500"}]) is True
    assert _has_failures([{"monthly_error": "0 kun"}]) is True


def test_has_failures_unsent_image():
    assert _has_failures([{"image": "x.png", "sent": False}]) is True


def test_auth_error_detection():
    assert _auth_error([{"error": "avtorizatsiya talab qilinadi"}]) is True
    assert _auth_error([{"monthly_error": "HTTP 401 talab"}]) is True
    assert _auth_error([{"error": "boshqa xato"}]) is False
