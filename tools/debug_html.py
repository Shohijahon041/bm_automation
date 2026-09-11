"""Debug dashboard HTML to find JS issues."""
import re

path = r"C:\Users\User\OneDrive\Документы\Default Project\bm_automation\app\dashboard\web\index.html"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Check CSS for .page display
m = re.search(r'\.page\s*\{([^}]*)\}', content)
if m:
    print("Page CSS:", m.group()[:200])
else:
    print("Page CSS: NOT FOUND")

# Check active-page
m2 = re.search(r'\.active-page\s*\{([^}]*)\}', content)
if m2:
    print("active-page CSS:", m2.group()[:200])
else:
    print("active-page CSS: NOT FOUND")

# Check pages
pages = re.findall(r'id="page-([^"]+)"', content)
print("Pages:", pages)

# Check for the "esc" function in the log viewer - possible conflict
# The log viewer uses escHtml but the rest uses esc
# Check if escHtml function body is valid
idx = content.find("function escHtml")
if idx >= 0:
    snippet = content[idx:idx+200]
    print("\nescHtml:", snippet[:150])

# Check what's rendered in the load() function - look for renderBoard etc
render_calls = re.findall(r'(\w+)\(', content[123000:140000])
render_funcs = [c for c in set(render_calls) if c.startswith('render')]
print("\nRender calls in second half:", render_funcs)

# Check if load() calls renderDashboard or similar
load_start = content.find("async function load()")
load_end = content.find("\n}\n", load_start)
load_body = content[load_start:load_end+3]
print("\nload() body (first 500 chars):")
print(load_body[:500])
