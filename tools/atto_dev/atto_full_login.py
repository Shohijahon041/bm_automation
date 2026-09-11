"""Fetch ATTO captcha, save, and wait for user input."""
import httpx, uuid, json

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()
from pathlib import Path

TOKEN = str(uuid.uuid4())
HEADERS = {
    "language": "uz",
    "token": TOKEN,
    "Origin": "https://dashboard.atto.uz",
    "Referer": "https://dashboard.atto.uz/login",
}

print(f"[TOKEN] {TOKEN}")

r = httpx.get(
    "https://adminpanel.atto.uz/v1.0/operator/login/get-captcha",
    params={"token": TOKEN},
    timeout=30,
    verify=False,
    headers=HEADERS,
)
print(f"[CAPTCHA] status={r.status_code}")

if r.status_code != 200:
    print("Captcha olinmadi!")
    exit(1)

p = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captcha.html")
html = '<html><body style="background:#1a1a2e;display:flex;justify-content:center;align-items:center;height:100vh"><div style="text-align:center"><div style="color:white;font-size:24px;margin-bottom:20px">ATTO Captcha - yeching va keyin login qiling:</div>' + r.text + '</div></body></html>'
p.write_text(html, encoding="utf-8")
print(f"[SAVED] {p}")

answer = input("\n>>> Captcha javobini kiriting: ").strip()
print(f"[ANSWER] {answer}")

# Login
payload = {
    "login": LOGIN,
    "password": PASSWORD,
    "code": answer,
    "token": TOKEN,
}
r = httpx.post(
    "https://adminpanel.atto.uz/v1.0/operator/captcha/login",
    json=payload,
    headers={**HEADERS, "Content-Type": "application/json"},
    timeout=30,
    verify=False,
)
print(f"[LOGIN] status={r.status_code}")
data = r.json()
print(f"[RESPONSE] {json.dumps(data, ensure_ascii=False)[:2000]}")

token_val = (
    data.get("data", {}).get("token")
    or data.get("accessToken")
    or data.get("data", {}).get("accessToken")
    or data.get("token")
)
if token_val:
    Path("C:/Users/User/OneDrive/Документы/Default Project/atto_tokens.json").write_text(
        json.dumps({"token": token_val}, indent=2), encoding="utf-8"
    )
    print(f"\n*** LOGIN MUVAFFAQIYATLI! Token: {token_val[:40]}...")
else:
    print("\n*** TOKEN TOPILMADI")
