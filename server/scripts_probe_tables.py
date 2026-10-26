import json
import sys

import pdfplumber

path = sys.argv[1]
with pdfplumber.open(path) as pdf:
    print("pages", len(pdf.pages))
    tables_total = 0
    sample = []
    for idx, page in enumerate(pdf.pages, start=1):
        tables = page.extract_tables()
        if tables:
            tables_total += len(tables)
            for t in tables:
                for row in t[:6]:
                    sample.append(f"p{idx} | " + " | ".join((c or "").replace("\n", " ") for c in row))
        if idx >= 8:
            break
    print("tables", tables_total)
    with open("/tmp/tables.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(sample[:80]))
    print("sample rows", len(sample))
