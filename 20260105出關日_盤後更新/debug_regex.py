
import re

samples = [
    "該有價證券之交易，連續三個營業日...約每五分鐘撮合一次...xxx",
    "處置措施：約每二十分鐘撮合一次，各證券商...",
    "處置措施：約每20分鐘撮合一次",
    "無此資訊"
]

regex = r'(約每\S+分鐘撮合一次)'

for s in samples:
    match = re.search(regex, s)
    if match:
        print(f"Match: '{s}' -> '{match.group(1)}'")
    else:
        print(f"No Match: '{s}'")
