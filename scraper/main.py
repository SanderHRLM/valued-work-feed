"""Dagelijkse run: per bedrijf vacatures ophalen, nieuwe beoordelen, verdwenen sluiten, feed schrijven.

Gebruik: python -m scraper.main [--only TEKST] [--region REGIO] [--limit N] [--no-llm]
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from . import ats, output
from .http import fetch_page_text, html_to_text

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "state.json"
TODAY = date.today().isoformat()

MAX_ACTIVE_PER_COMPANY = 2   # zoveel vacatures per bedrijf in de feed
SPARE_CANDIDATES = 3         # zoveel extra kandidaten beoordelen als de eerste keus afvalt
CLOSE_AFTER_MISSING = 2   # zoveel geslaagde runs achter elkaar niet meer gezien: gesloten
WORKERS = 8
ALLOWED_TAGS = {"p", "h3", "h4", "ul", "ol", "li", "strong", "em", "b", "i", "br"}

lock = threading.Lock()


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"companies": {}, "jobs": {}}


def save_state(state):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def norm_url(url):
    u = urlparse(url.strip())
    query = "&".join(p for p in u.query.split("&") if p and not p.lower().startswith(("utm_", "source=", "ref=")))
    return urlunparse((u.scheme, u.netloc.lower(), u.path.rstrip("/") or "/", "", query, ""))


def clean_html(raw):
    soup = BeautifulSoup(raw or "", "html.parser")
    for t in soup(["script", "style", "iframe", "form", "img"]):
        t.decompose()
    for t in soup.find_all(True):
        if t.name in ("h1", "h2"):
            t.name = "h3"
        if t.name == "div":
            t.name = "p"
        if t.name not in ALLOWED_TAGS:
            t.unwrap()
        else:
            t.attrs = {}
    return re.sub(r"\s+\n", "\n", str(soup)).strip()


def company_key(c):
    return "%s|%s" % (c["region"], c["company"])


def list_candidates(c, cstate, use_llm):
    """Geeft (kandidaten, bron). Gooit een fout als de bron niet bruikbaar was."""
    target = c["ats_url"] or cstate.get("ats_url")
    found = ats.detect(target) if target else None
    url = c["careers_url"] or c["website"]
    if found:
        try:
            return ats.fetch(*found), "ats:" + found[0]
        except Exception:  # noqa: BLE001 - feed niet beschikbaar: lees de lijstpagina zelf uit
            url = ats.fallback_page(*found) or url
            if not url or (ats.detect(url) and not ats.fallback_page(*found)):
                raise
    if not url:
        raise Skip("geen carrièrepagina bekend")
    if "linkedin.com" in url:
        raise Skip("alleen LinkedIn (niet uit te lezen zonder login)")
    try:
        return _generic(c, cstate, url, use_llm, depth=0)
    except Skip:
        raise
    except Exception:  # noqa: BLE001 - carrièrepagina verhuisd of stuk: zoek de link via de homepage
        home = c["website"]
        if not home or norm_url(home) == norm_url(url):
            raise
        return _generic(c, cstate, home, use_llm, depth=0)


def _generic(c, cstate, url, use_llm, depth):
    text, how = fetch_page_text(url)
    if len(text) < 80:
        raise RuntimeError("pagina leeg of geblokkeerd")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    cache = cstate.get("pages", {}).get(url)
    if cache and cache["hash"] == digest:
        res = cache["result"]
    elif not use_llm:
        raise Skip("pagina gewijzigd; Claude nodig (--no-llm)")
    else:
        from . import llm

        res = llm.extract_listing(c["company"], url, text)
        if res is None:
            raise RuntimeError("Claude weigerde de pagina")
        cstate.setdefault("pages", {})[url] = {"hash": digest, "result": res}
    kind = res["page_kind"]
    if kind == "links_elsewhere" and res.get("follow_url") and depth == 0 and res["follow_url"] in text:
        follow = res["follow_url"]
        found = ats.detect(follow)
        if found:
            cstate["ats_url"] = follow
            return ats.fetch(*found), "ats:" + found[0]
        if "linkedin.com" in follow:
            raise Skip("verwijst naar LinkedIn")
        return _generic(c, cstate, follow, use_llm, depth=1)
    if kind in ("blocked_or_empty", "not_a_careers_page", "links_elsewhere"):
        raise RuntimeError("geen vacaturelijst gevonden (%s)" % kind)
    cands = []
    for j in res["jobs"]:
        # Alleen links die echt op de pagina staan; voorkomt verzonnen URL's.
        if j["url"] and j["url"] in text and j["title"].strip():
            if norm_url(j["url"]) == norm_url(url):
                j = dict(j, url=url.split("#")[0] + "#" + re.sub(r"[^a-z0-9]+", "-", j["title"].lower()).strip("-"))
            cands.append(j)
    return cands, "pagina:" + how


class Skip(Exception):
    pass


def process_company(c, state, use_llm, log):
    key = company_key(c)
    with lock:
        cstate = state["companies"].setdefault(key, {})
    try:
        cands, source = list_candidates(c, cstate, use_llm)
    except Skip as e:
        cstate.update(status="overgeslagen", note=str(e))
        return
    except Exception as e:  # noqa: BLE001
        cstate.update(status="fout", note="%s: %s" % (type(e).__name__, str(e)[:200]),
                      fails=cstate.get("fails", 0) + 1)
        return
    seen = {}
    for cand in cands:
        if cand.get("url", "").startswith("http"):
            seen.setdefault(norm_url(cand["url"]) + ("#" + cand["url"].split("#", 1)[1] if "#" in cand["url"] else ""), cand)
    with lock:
        mine = {k: j for k, j in state["jobs"].items() if j["company_key"] == key}
    # Zusterbedrijven delen soms één vacaturesite: een vacature die een ander bedrijf afwees
    # mag dit bedrijf één keer zelf beoordelen.
    def is_new(k):
        j = state["jobs"].get(k)
        if j is None:
            return True
        # "skipped" = wel gezien maar nooit beoordeeld (geen plek): telt opnieuw mee zodra er plek is.
        return j["status"] == "skipped" or (
            j["status"] == "rejected" and key not in j.get("judged_by", [j["company_key"]]))

    # Staan er meer in de feed dan het maximum (bijv. na het verlagen ervan), kies dan opnieuw.
    if sum(1 for j in mine.values() if j["status"] == "active") > MAX_ACTIVE_PER_COMPANY:
        for j in mine.values():
            if j["status"] == "active":
                j.update(status="skipped", reason="opnieuw kiezen: boven het maximum per bedrijf", description_html="")
    new = [(k, cand) for k, cand in seen.items() if is_new(k)]
    for k, j in mine.items():
        if k in seen:
            j["last_seen"], j["missing"] = TODAY, 0
            if j["status"] == "closed":
                j["status"] = "active"
        else:
            j["missing"] = j.get("missing", 0) + 1
            if j["status"] == "active" and j["missing"] >= CLOSE_AFTER_MISSING:
                j["status"], j["closed_on"] = "closed", TODAY
    active = sum(1 for j in mine.values() if j["status"] == "active")
    added = 0
    slots = MAX_ACTIVE_PER_COMPANY - active
    if new and use_llm and slots > 0:
        from . import llm

        # Meer kandidaten dan plekken: Claude kiest op titel de beste, in volgorde van voorkeur.
        if len(new) > slots:
            order = list(dict.fromkeys(llm.triage(c, [cand for _, cand in new], slots + SPARE_CANDIDATES)))
            new = [new[i] for i in order]
        for k, cand in new[: slots + SPARE_CANDIDATES]:
            if added >= slots:
                break
            try:
                added += _assess_and_store(state, k, c, cand, llm)
            except Exception as e:  # noqa: BLE001 - één vacature mag de rest niet tegenhouden
                log.append("%s | %s: %s" % (c["company"], cand["title"], str(e)[:160]))
    cstate.update(status="ok", note=source, fails=0, last_ok=TODAY, listed=len(seen), new=added)


def drop_duplicates(state, log):
    """Zusterbedrijven zetten dezelfde vacature soms op elk hun eigen vacaturesite: houd er één."""
    first = {}
    for k in sorted(state["jobs"], key=lambda k: (state["jobs"][k]["first_seen"], state["jobs"][k]["company_key"])):
        j = state["jobs"][k]
        if j["status"] != "active":
            continue
        u = urlparse(j["url"])
        path = u.path.rstrip("/")
        sigs = [(j["title"].casefold().strip(), path)]
        # Zelfde vacaturenummer bij hetzelfde vacaturesysteem (bijv. twee Personio-subdomeinen).
        if re.search(r"/\d{6,}$", path):
            sigs.append((".".join(u.netloc.lower().split(".")[-2:]), path))
        hit = next((first[s] for s in sigs if s in first and first[s]["company_key"] != j["company_key"]), None)
        if hit:
            j.update(status="rejected", reason="dubbel met %s" % hit["company"], description_html="")
            log.append("%s | %s: dubbel met %s, niet opgenomen" % (j["company"], j["title"], hit["company"]))
        else:
            for s in sigs:
                first.setdefault(s, j)


def _assess_and_store(state, k, c, cand, llm):
    desc = cand.get("description_html") or ""
    if not desc and cand.get("detail"):
        desc = ats.fetch_detail(cand["detail"])
    if desc:
        text = html_to_text(desc, cand["url"], keep_links=False)
    else:
        text, _ = fetch_page_text(cand["url"].split("#")[0])
    res = llm.assess(c, cand, text, write_description=not desc)
    if res is None:
        return 0
    ok = res["is_open_vacancy"] and res["relevant"]
    body = clean_html(desc or res["description_html"])
    if ok and len(BeautifulSoup(body, "html.parser").get_text()) < 200:
        ok, res["reason"] = False, "geen bruikbare vacaturetekst"
    _store(state, k, c, cand, "active" if ok else "rejected", reason=res["reason"], title=res["title"] or cand["title"],
           category=res["category"], job_type=res["job_type"], location=res["location"] or cand.get("location", ""),
           remote=bool(res["remote"] or cand.get("remote")), description_html=body if ok else "")
    return 1 if ok else 0


def _store(state, k, c, cand, status, **extra):
    job = {
        "company_key": company_key(c), "company": c["company"], "company_url": c["website"], "region": c["region"],
        "country": c["country"], "title": cand["title"], "url": cand["url"].split("#")[0],
        "location": cand.get("location", ""), "status": status, "first_seen": TODAY, "last_seen": TODAY, "missing": 0,
    }
    job.update(extra)
    with lock:
        old = state["jobs"].get(k)
        judged = (old.get("judged_by", [old["company_key"]]) if old else []) + [job["company_key"]]
        if old and status != "active" and old["status"] != "skipped":
            old["judged_by"] = judged
            return
        job["judged_by"] = judged
        state["jobs"][k] = job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--region")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    companies = list(csv.DictReader((ROOT / "companies.csv").open(encoding="utf-8")))
    if args.region:
        companies = [c for c in companies if c["region"] == args.region]
    if args.only:
        companies = [c for c in companies if args.only.lower() in c["company"].lower()]
    if args.limit:
        companies = companies[: args.limit]

    state, log = load_state(), []
    # Gewijzigde selectieregels: alles opnieuw laten kiezen, zodat de feed de nieuwe regels volgt.
    rules = hashlib.sha1(((ROOT / "criteria.md").read_text(encoding="utf-8") + str(MAX_ACTIVE_PER_COMPANY))
                         .encode("utf-8")).hexdigest()
    if state.get("rules") != rules:
        for j in state["jobs"].values():
            if j["status"] in ("active", "rejected"):
                j.update(status="skipped", reason="opnieuw kiezen: selectieregels gewijzigd", description_html="")
                j.pop("judged_by", None)
        state["rules"] = rules
        log.append("Selectieregels gewijzigd: alle vacatures worden opnieuw gekozen.")

    def run(c):
        try:
            process_company(c, state, not args.no_llm, log)
        except Exception:  # noqa: BLE001
            log.append("%s: %s" % (c["company"], traceback.format_exc(limit=2)))
            state["companies"].setdefault(company_key(c), {}).update(status="fout", note="onverwachte fout")

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(run, companies))

    drop_duplicates(state, log)
    save_state(state)
    stats = output.write_all(state, [company_key(c) for c in companies], log, ROOT)
    print(stats["summary"])
    # Laat de workflow falen (GitHub mailt dan) als de run als geheel onbetrouwbaar was.
    tried = stats["ok"] + stats["errors"]
    if tried >= 10 and stats["errors"] > 0.5 * tried:
        sys.exit("Meer dan de helft van de bedrijven gaf een fout; feed niet vertrouwen.")


if __name__ == "__main__":
    main()
