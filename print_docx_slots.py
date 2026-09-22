from __future__ import annotations

import json
import sys
from pathlib import Path


for item in sys.argv[1:]:
    path = Path(item)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n===== {path.name} =====")
    print("BODY PARAGRAPHS")
    for i, p in enumerate(data["paragraphs"]):
        text = p["text"].replace("\n", " ").strip()
        if text:
            print(f"P{i:03d} [{p['style']}]: {text}")
    print("TABLES")
    for table in data["tables"]:
        print(f"--- TABLE {table['index']} ---")
        for row in table["rows"]:
            ri = row[0]["row"] if row else -1
            vals = [cell["text"].replace("\n", " | ").strip() for cell in row]
            print(f"R{ri:02d}: " + " || ".join(vals))
    print("HEADERS")
    for si, header in enumerate(data["headers"]):
        vals = [p["text"].strip() for p in header if p["text"].strip()]
        print(si, vals)
    print("FOOTERS")
    for si, footer in enumerate(data["footers"]):
        vals = [p["text"].strip() for p in footer if p["text"].strip()]
        print(si, vals)
