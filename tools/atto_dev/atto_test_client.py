import json, sys
sys.path.insert(0, '.')
from datetime import date
from bm_automation.app.api.atto_client import ATTOClient

c = ATTOClient()
c.token = '7ba3fc18-74b9-4010-9556-09995229e47b'

print('Profile:', c.profile().get('data', {}).get('login', '?'))

report = c.bus_report(from_date=date(2026, 8, 18), to_date=date(2026, 8, 18))
data = report.get('data', {})
total = data.get('totalElements', 0)
content = data.get('content', [])
print('Bus report:', total, 'total,', len(content), 'in page')
if content:
    first = content[0]
    print('  First:', first.get('eventDate', '?'), 'amount=', first.get('amount', '?'))

counts = c.all_counts(from_date=date(2026, 8, 18), to_date=date(2026, 8, 18))
items = counts.get('data', {}).get('items', [])
print('All counts:', len(items), 'merchants')
for item in items:
    for route in item.get('routes', []):
        for bus in route.get('buses', []):
            print('  ', bus.get('name'), ': cards=', bus.get('cards', 0))

# Z-report
z = c.z_report(d=date(2026, 8, 18), dep_id='60863GCA')
zd = z.get('data', {})
print('Z-report:', zd.get('vehicle', '?'), 'total=', zd.get('totalSum', 0))
