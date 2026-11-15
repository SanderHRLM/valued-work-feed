"""Bouwt een testversie van public/ met echte vacatures uit een paar vacaturesystemen, zonder Claude.

Alleen bedoeld om de koppeling met JBoard te proberen; een echte run overschrijft dit.
"""
import csv
import sys
from pathlib import Path

from scraper import ats, output
from scraper.main import ROOT, TODAY, clean_html, company_key, norm_url

NAMES = sys.argv[1:] or ["Prezzee", "NeoDay", "Monizze", "Prizeout", "Payflip"]
state = {"companies": {}, "jobs": {}}
for c in csv.DictReader((ROOT / "companies.csv").open(encoding="utf-8")):
    if c["company"] not in NAMES:
        continue
    for cand in ats.fetch(*ats.detect(c["ats_url"]))[:4]:
        if not cand.get("description_html"):
            continue
        state["jobs"][norm_url(cand["url"])] = {
            "company_key": company_key(c), "company": c["company"], "company_url": c["website"],
            "region": c["region"], "country": c["country"], "title": cand["title"], "url": cand["url"],
            "location": cand.get("location", ""), "remote": bool(cand.get("remote")), "status": "active",
            "first_seen": TODAY, "last_seen": TODAY, "category": "Gift card jobs", "job_type": "Full-time",
            "description_html": clean_html(cand["description_html"]),
        }
(ROOT / "data").mkdir(exist_ok=True)
print(output.write_all(state, [], [], ROOT)["summary"])
(ROOT / "data" / "last_run.md").unlink()
