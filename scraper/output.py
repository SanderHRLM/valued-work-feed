"""Schrijft de feed (XML + JSON), een eenvoudige overzichtspagina en het runrapport."""
import hashlib
import html
import json
import os
from collections import Counter
from datetime import datetime, timezone
from xml.sax.saxutils import escape


def job_id(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def cdata(text):
    return "<![CDATA[" + (text or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


# Opbouw volgt wat JBoard's Web Page Scraper bij bekende vacaturesystemen aantreft: een lijstpagina met
# absolute links, en per vacature een pagina met JobPosting-gegevens.
PUBLIC_URL = os.environ.get("VW_PUBLIC_URL", "https://sanderhrlm.github.io/valued-work-feed/").rstrip("/") + "/"
PAGE = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>%s</title>\n'
    "<style>body{font:16px/1.5 system-ui;max-width:760px;margin:2rem auto;padding:0 16px}"
    ".job{border-bottom:1px solid #ddd;padding:.6rem 0}h2{font-size:1.05rem;margin:0}.job p{margin:0}"
    ".meta{color:#666;font-size:.85rem}</style>\n%s\n</head>\n<body>\n%s\n</body>\n</html>\n"
)
# Bezoekers die vanaf valued.work op "apply" klikken gaan direct door naar de werkgever.
REDIRECT = '<script>if(/valued\\.work/.test(document.referrer)){location.replace(%s)}</script>'
EMPLOYMENT = {"Full-time": "FULL_TIME", "Part-time": "PART_TIME", "Internship": "INTERN", "Contract": "CONTRACTOR",
              "Temporary": "TEMPORARY"}


def json_ld(jid, j):
    org = {"@type": "Organization", "name": j["company"]}
    if j.get("company_url"):
        org["sameAs"] = j["company_url"]
    address = {"@type": "PostalAddress"}
    if j.get("location"):
        address["addressLocality"] = j["location"]
    if j.get("country"):
        address["addressCountry"] = j["country"]
    ld = {
        "@context": "http://schema.org", "@type": "JobPosting", "title": j["title"],
        "hiringOrganization": org, "jobLocation": {"@type": "Place", "address": address},
        "employmentType": EMPLOYMENT.get(j.get("job_type"), "FULL_TIME"), "datePosted": j["first_seen"],
        "description": j.get("description_html", ""),
    }
    if j.get("remote"):
        ld["jobLocationType"] = "TELECOMMUTE"
        if j.get("country"):
            ld["applicantLocationRequirements"] = {"@type": "Country", "name": j["country"]}
    return ld


def ld_script(ld):
    return '<script type="application/ld+json">%s</script>' % json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")


def write_all(state, run_keys, log, root):
    pub = root / "public"
    pub.mkdir(exist_ok=True)
    active = sorted(
        ((k, j) for k, j in state["jobs"].items() if j["status"] == "active"),
        key=lambda kj: (kj[1]["first_seen"], kj[1]["company"]), reverse=True,
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    items = []
    for k, j in active:
        items.append(
            "<job>"
            "<id>%s</id><title>%s</title><company>%s</company><company_url>%s</company_url>"
            "<url>%s</url><location>%s</location><country>%s</country><region>%s</region><remote>%s</remote>"
            "<category>%s</category><job_type>%s</job_type><posted_at>%s</posted_at>"
            "<description>%s</description></job>"
            % (job_id(k), escape(j["title"]), escape(j["company"]), escape(j.get("company_url", "")),
               escape(j["url"]), escape(j.get("location", "")), escape(j.get("country", "")), escape(j["region"]),
               "true" if j.get("remote") else "false", escape(j.get("category", "")), escape(j.get("job_type", "")),
               j["first_seen"], cdata(j.get("description_html", "")))
        )
    (pub / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<jobs generated="%s" count="%d">\n%s\n</jobs>\n'
        % (now, len(items), "\n".join(items)), encoding="utf-8")
    (pub / "jobs.json").write_text(
        json.dumps({"generated": now, "jobs": [dict(j, id=job_id(k)) for k, j in active]}, ensure_ascii=False),
        encoding="utf-8")

    # JBoard's Web Page Scraper leest schema.org JobPosting-gegevens. Die staan op de overzichtspagina
    # (alle vacatures) en op een eigen pagina per vacature.
    jobs_dir = pub / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    keep, rows, all_ld = set(), [], []
    for k, j in active:
        jid = job_id(k)
        keep.add(jid + ".html")
        ld = json_ld(jid, j)
        all_ld.append(ld)
        meta = "%s · %s · %s" % (html.escape(j.get("category", "")), html.escape(j.get("job_type", "")), j["first_seen"])
        rows.append(
            '<article class="job"><h2><a class="posting-title" href="%sjobs/%s.html">%s</a></h2><p class="company">%s</p>'
            '<p class="location">%s</p><p class="meta">%s</p></article>'
            % (PUBLIC_URL, jid, html.escape(j["title"]), html.escape(j["company"]), html.escape(j.get("location", "")),
               meta))
        (jobs_dir / (jid + ".html")).write_text(
            PAGE % (html.escape(j["title"] + " at " + j["company"]), ld_script(ld) + REDIRECT % json.dumps(j["url"]),
                    "<h1>%s</h1><p class=\"company\">%s</p><p class=\"location\">%s</p><p class=\"meta\">%s</p>"
                    "<p><a class=\"apply\" href=\"%s\">Apply at %s</a></p><div class=\"description\">%s</div>"
                    % (html.escape(j["title"]), html.escape(j["company"]), html.escape(j.get("location", "")), meta,
                       html.escape(j["url"]), html.escape(j["company"]), j.get("description_html", ""))),
            encoding="utf-8")
    for old in jobs_dir.glob("*.html"):
        if old.name not in keep:
            old.unlink()
    (pub / "index.html").write_text(
        PAGE % ("valued.work job feed", "",
                "<h1>valued.work job feed</h1><p>%d open jobs · updated %s</p>%s" % (len(active), now, "\n".join(rows))),
        encoding="utf-8")

    comp = {k: state["companies"].get(k, {}) for k in run_keys}
    by_status = Counter(c.get("status", "niet gedraaid") for c in comp.values())
    today = now[:10]
    new_today = [j for _, j in active if j["first_seen"] == today]
    closed_today = [j for j in state["jobs"].values() if j.get("closed_on") == today]
    lines = [
        "# Run %s" % now, "",
        "- Actieve vacatures in feed: **%d**" % len(active),
        "- Nieuw vandaag: **%d**, gesloten vandaag: **%d**" % (len(new_today), len(closed_today)),
        "- Bedrijven: %d ok, %d fout, %d overgeslagen" % (by_status["ok"], by_status["fout"], by_status["overgeslagen"]),
    ]
    try:
        from . import llm

        u = llm.usage
        lines.append("- Claude (%s): %d aanroepen, %d invoer- en %d uitvoertokens, %d afgekapte pagina's, %d weigeringen"
                     % (llm.MODEL, u["calls"], u["input"], u["output"], u["truncated_pages"], u["refusals"]))
    except Exception:  # noqa: BLE001 - zonder anthropic-pakket (bijv. --no-llm) is er geen verbruik
        pass
    if new_today:
        lines += ["", "## Nieuw"] + ["- %s — [%s](%s)" % (j["company"], j["title"], j["url"]) for j in new_today]
    if closed_today:
        lines += ["", "## Gesloten"] + ["- %s — %s" % (j["company"], j["title"]) for j in closed_today]
    for status, title in (("fout", "Fouten"), ("overgeslagen", "Overgeslagen")):
        sel = sorted((k, c) for k, c in comp.items() if c.get("status") == status)
        if sel:
            lines += ["", "## %s (%d)" % (title, len(sel))] + ["- %s: %s" % (k, c.get("note", "")) for k, c in sel]
    if log:
        lines += ["", "## Meldingen"] + ["- " + m for m in log]
    report = "\n".join(lines) + "\n"
    (root / "data" / "last_run.md").write_text(report, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report[:900_000])
    return {"ok": by_status["ok"], "errors": by_status["fout"], "summary": "\n".join(lines[:6])}
