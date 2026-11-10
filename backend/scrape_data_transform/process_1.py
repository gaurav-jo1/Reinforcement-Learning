import json
from pathlib import Path

HAR_FILE = Path("kite.zerodha.com.har")
OUTPUT_FILE = Path("all_responses.json")

with HAR_FILE.open("r", encoding="utf-8") as f:
    har = json.load(f)

entries = har["log"]["entries"]

responses = []

for index, entry in enumerate(entries):
    request = entry.get("request", {})
    response = entry.get("response", {})
    content = response.get("content", {})

    body = content.get("text")

    if body is None:
        continue

    responses.append({
        "index": index,
        "url": request.get("url"),
        "status": response.get("status"),
        "content_type": content.get("mimeType"),
        "body": body,
    })

with OUTPUT_FILE.open("w", encoding="utf-8") as f:
    json.dump(responses, f, indent=2, ensure_ascii=False)

print(f"Total requests: {len(entries)}")
print(f"Responses extracted: {len(responses)}")
print(f"Output file: {OUTPUT_FILE}")