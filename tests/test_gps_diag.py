"""Oqim diagnostikasi yordamchilari: ffmpeg logining oxirgi ma'noli qatori.

Dashboard \"oxirgi xato\"ni shu funksiya orqali ko'rsatadi — shuning uchun
ahamiyatsiz (doimiy) ogohlantirishlar filtrlanadi va hech qanday holatda
xato otilmaydi (diagnostika asosiy oqimni to'xtatmasligi kerak).
"""

from bm_automation.app.gps import diag


def test_tail_note_missing_file():
    assert diag.tail_note(None) is None
    assert diag.tail_note("/yo'q/bunday/fayl.log") is None


def test_tail_note_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_bytes(b"")
    assert diag.tail_note(p) is None


def test_tail_note_skips_benign_lines(tmp_path):
    """Faqat doimiy ogohlantirishlar bo'lsa — \"xato\" yo'q."""
    p = tmp_path / "benign.log"
    p.write_bytes(
        b"[h264 @ 0x1] non monotonically increasing dts to muxer in stream 0\n"
        b"[libx264 @ 0x2] The -vsync option is deprecated\n"
        b"[mpegts @ 0x3] Timestamps are unset in a packet for stream 0\n")
    assert diag.tail_note(p) is None


def test_tail_note_returns_last_meaningful_line(tmp_path):
    p = tmp_path / "mixed.log"
    p.write_bytes(
        b"[h264 @ 0x1] non monotonically increasing dts to muxer in stream 0\n"
        b"[rtsp @ 0x2] method DESCRIBE failed: 401 Unauthorized\n"
        b"\n")
    assert diag.tail_note(p) == \
        "[rtsp @ 0x2] method DESCRIBE failed: 401 Unauthorized"


def test_tail_note_reads_only_tail_of_large_file(tmp_path):
    """Katta loglar ham to'liq o'qilmaydi — faqat oxirgi bo'lak."""
    p = tmp_path / "big.log"
    p.write_bytes(b"[h264 @ 0x1] eski xato\n" * 5000 +
                  b"[hls @ 0x9] oxirgi xato\n")
    assert diag.tail_note(p) == "[hls @ 0x9] oxirgi xato"


def test_tail_note_truncates_long_line(tmp_path):
    p = tmp_path / "long.log"
    p.write_bytes(b"x" * 1000 + b"\n")
    note = diag.tail_note(p)
    assert note is not None and len(note) == diag.MAX_LINE
