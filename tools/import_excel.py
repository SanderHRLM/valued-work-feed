"""Zet de regio-Excelbestanden om naar companies.csv (eenmalig, of opnieuw als de lijsten wijzigen).

Gebruik: python -m tools.import_excel REGIO=pad.xlsx [REGIO=pad.xlsx ...]
"""
import csv
import sys
from pathlib import Path

import openpyxl

from scraper import ats

FIELDS = ["region", "company", "website", "country", "city", "category", "description", "careers_url", "ats_url"]
ALIASES = {
    "company": ["company"],
    "website": ["website", "company website"],
    "country": ["country"],
    "city": ["city / hq"],
    "category": ["category"],
    "description": ["description"],
    "careers_url": ["careers page", "careers / job board url"],
}


def main(args):
    rows, seen = [], set()
    for arg in args:
        region, path = arg.split("=", 1)
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
        data = list(ws.iter_rows(values_only=True))
        header = [str(c or "").strip().lower() for c in data[0]]
        col = {f: next((header.index(a) for a in al if a in header), None) for f, al in ALIASES.items()}
        for r in data[1:]:
            if not r or not r[col["company"]]:
                continue
            row = {f: (str(r[i]).strip() if i is not None and i < len(r) and r[i] else "") for f, i in col.items()}
            for f in ("website", "careers_url"):
                if not row[f].startswith("http"):
                    row[f] = ""
            row["region"] = region
            if region == "USA" and not row["country"]:
                row["country"] = "United States"
            urls = [str(c).strip() for c in r if c and str(c).strip().startswith("http")]
            row["ats_url"] = ""
            for u in [row["careers_url"]] + urls:
                if ats.detect(u):
                    row["ats_url"] = u
                    break
            key = (row["company"].lower(), row["careers_url"].lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    out = Path("companies.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print("%d bedrijven naar %s" % (len(rows), out))


if __name__ == "__main__":
    main(sys.argv[1:])
