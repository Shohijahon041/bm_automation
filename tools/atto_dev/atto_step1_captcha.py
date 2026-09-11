"""Step 1: Fetch ATTO captcha."""
import httpx, uuid, json
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

if r.status_code != 200:
    print("ERROR")
    exit(1)

p = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captcha.html")
html = '<html><body style="background:#1a1a2e;display:flex;justify-content:center;align-items:center;height:100vh"><div style="text-align:center"><div style="color:white;font-size:24px;margin-bottom:20px">ATTO Captcha - yeching:</div>' + r.text + '</div></body></html>'
p.write_text(html, encoding="utf-8")

# Save token for step 2
tp = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captcha_token.txt")
tp.write_text(TOKEN, encoding="utf-8")

print(f"TOKEN={TOKEN}")
print(f"SAVED={p}")
