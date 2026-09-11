"""Interactive ATTO login — get captcha, display, ask user, then login."""
import httpx, uuid, json, re, sys

from atto_env import atto_credentials

LOGIN, PASSWORD = atto_credentials()

BASE = "https://adminpanel.atto.uz/v1.0/operator"
TOKEN = str(uuid.uuid4())
HEADERS = {"language": "uz", "token": TOKEN, "Origin": "https://dashboard.atto.uz", "Referer": "https://dashboard.atto.uz/login"}

print(f"[1] Client token: {TOKEN}")

# 1. Get captcha
r = httpx.get(f"{BASE}/login/get-captcha", params={"token": TOKEN}, timeout=30, verify=False, headers=HEADERS)
print(f"[2] Captcha status: {r.status_code}, CT: {r.headers.get('content-type')}")
svg = r.text

# Save SVG for viewing
from pathlib import Path
p = Path(__file__).resolve().parent / "atto_captcha.svg"
p.write_text(svg, encoding="utf-8")
print(f"[3] Captcha SVG saved to: {p}")

# Try to extract text characters from SVG path data
# Look for text elements
text_matches = re.findall(r"<text[^>]*>(.*?)</text>", svg)
if text_matches:
    print(f"[4] Visible text: {''.join(text_matches)}")

# 2. Ask user
answer = input("\n>>> Captcha javobini kiriting (5 ta belgi): ").strip()

# 3. Login
payload = {
    "login": LOGIN,
    "password": PASSWORD,
    "code": answer,
    "token": TOKEN,
}
print(f"\n[5] Sending login with: login={payload['login']}, code={answer}, token={TOKEN[:20]}...")

r = httpx.post(
    f"{BASE}/captcha/login",
    json=payload,
    headers={**HEADERS, "Content-Type": "application/json"},
    timeout=30,
    verify=False,
)
print(f"[6] Login status: {r.status_code}")
data = r.json()
print(f"[7] Login response: {json.dumps(data, ensure_ascii=False)[:1000]}")

token_val = None
if isinstance(data, dict):
    token_val = (
        data.get("data", {}).get("token")
        or data.get("accessToken")
        or data.get("data", {}).get("accessToken")
        or data.get("token")
    )
if token_val:
    print(f"\n*** TOKEN: {token_val[:60]}...")
    # Save
    Path("atto_tokens.json").write_text(json.dumps({"token": token_val}, indent=2), encoding="utf-8")
    print("*** Token saved to atto_tokens.json")
else:
    print("\n*** TOKEN TOPILMADI")
