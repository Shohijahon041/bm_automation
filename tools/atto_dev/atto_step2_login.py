"""Step 2: Login with captcha answer."""
import httpx, json

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()
from pathlib import Path

TOKEN = Path("C:/Users/User/OneDrive/Документы/Default Project/atto_captcha_token.txt").read_text(encoding="utf-8").strip()
ANSWER = "49297"

HEADERS = {"language": "uz", "token": TOKEN, "Origin": "https://dashboard.atto.uz", "Referer": "https://dashboard.atto.uz/login"}

payload = {
    "login": LOGIN,
    "password": PASSWORD,
    "code": ANSWER,
    "token": TOKEN,
}

r = httpx.post(
    "https://adminpanel.atto.uz/v1.0/operator/captcha/login",
    json=payload,
    headers={**HEADERS, "Content-Type": "application/json"},
    timeout=30,
    verify=False,
)
print(f"STATUS={r.status_code}")
data = r.json()
print(json.dumps(data, ensure_ascii=False)[:2000])

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
    print(f"LOGIN_OK token={token_val[:50]}...")
else:
    print("NO_TOKEN")
