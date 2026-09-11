"""Fetch ATTO captcha SVG and save for viewing."""
import httpx, uuid
from pathlib import Path

TOKEN = str(uuid.uuid4())
HEADERS = {
    "language": "uz",
    "token": TOKEN,
    "Origin": "https://dashboard.atto.uz",
    "Referer": "https://dashboard.atto.uz/login",
}

r = httpx.get(
    "https://adminpanel.atto.uz/v1.0/operator/login/get-captcha",
    params={"token": TOKEN},
    timeout=30,
    verify=False,
    headers=HEADERS,
)
print(f"STATUS: {r.status_code}")
print(f"TOKEN: {TOKEN}")

if r.status_code == 200:
    p = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captcha.svg")
    p.write_text(r.text, encoding="utf-8")
    print(f"SVG saved to {p} ({len(r.text)} bytes)")

    html = '<html><body style="background:#1a1a2e;display:flex;justify-content:center;align-items:center;height:100vh"><div style="text-align:center"><div style="color:white;font-size:24px;margin-bottom:20px">ATTO Captcha - yeching:</div>' + r.text + '</div></body></html>'
    hp = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captcha.html")
    hp.write_text(html, encoding="utf-8")
    print(f"HTML saved to {hp}")
else:
    print(r.text[:500])
