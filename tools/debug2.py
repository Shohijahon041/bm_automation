"""Find renderPills function."""
import re

path = r"C:\Users\User\OneDrive\Документы\Default Project\bm_automation\app\dashboard\web\index.html"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Find renderPills
m = re.search(r'function renderPills\(\)', content)
start = m.start()
depth = 0
end = start
for i in range(start, min(start + 3000, len(content))):
    if content[i] == '{':
        depth += 1
    elif content[i] == '}':
        depth -= 1
    if depth == 0:
        end = i
        break
print(content[start:end+1][:2000])
