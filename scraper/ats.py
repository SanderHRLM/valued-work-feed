"""Koppelingen met standaard vacaturesystemen (ATS) die een publieke feed hebben.

detect(url) geeft (soort, sleutel) of None. fetch(soort, sleutel) geeft een lijst
kandidaten: dicts met title, url, location, en description_html of detail.
"""
import html
import re
import threading
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from .http import get, post_json

MAX_JOBS_PER_BOARD = 300


def detect(url):
    if not url:
        return None
    u = urlparse(url.strip())
    host = u.netloc.lower()
    parts = [p for p in u.path.split("/") if p]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io") and parts:
        if parts[0] != "embed":
            return ("greenhouse", parts[0])
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return ("lever", ("eu" if ".eu." in host else "us") + ":" + parts[0])
    if host == "apply.workable.com" and parts and parts[0] not in ("api", "j"):
        return ("workable", parts[0])
    m = re.match(r"^([a-z0-9-]+)\.jobs\.personio\.(de|com)$", host)
    if m:
        return ("personio", host)
    m = re.match(r"^([a-z0-9-]+)\.recruitee\.com$", host)
    if m:
        return ("recruitee", m.group(1))
    if host == "jobs.ashbyhq.com" and parts:
        return ("ashby", parts[0])
    if host in ("careers.smartrecruiters.com", "jobs.smartrecruiters.com") and parts:
        return ("smartrecruiters", parts[0])
    m = re.match(r"^([a-z0-9-]+)\.teamtailor\.com$", host)
    if m:
        return ("teamtailor", host)
    if host.endswith(".myworkdayjobs.com") and parts:
        tenant = host.split(".")[0]
        segs = [p for p in parts if not re.match(r"^[a-z]{2}(-[A-Z]{2})?$", p)]
        if segs:
            return ("workday", _wd_key(host, tenant, segs[0], u.query))
    if host.endswith(".myworkdaysite.com") and "recruiting" in parts:
        i = parts.index("recruiting")
        if len(parts) > i + 2:
            return ("workday", _wd_key(host, parts[i + 1], parts[i + 2], u.query))
    return None


def _wd_key(host, tenant, site, query):
    return "|".join([host, tenant, site, query or ""])


def fallback_page(kind, key):
    """Publieke lijstpagina van het systeem, voor als de feed niet beschikbaar is."""
    if kind in ("personio", "teamtailor"):
        return "https://%s/" % key
    return None


_cache, _locks, _guard = {}, {}, threading.Lock()


def fetch(kind, key):
    """Haalt een vacaturesite op. Meerdere bedrijven delen soms één site (bijv. alle Pluxee-landen):
    die wordt per run één keer opgehaald, zodat de site ons niet afknijpt."""
    with _guard:
        lock = _locks.setdefault((kind, key), threading.Lock())
    with lock:
        if (kind, key) not in _cache:
            _cache[(kind, key)] = globals()["_" + kind](key)[:MAX_JOBS_PER_BOARD]
        return [dict(j) for j in _cache[(kind, key)]]


def _greenhouse(token):
    data = get("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % token).json()
    return [
        {
            "title": j["title"].strip(),
            "url": j["absolute_url"],
            "location": (j.get("location") or {}).get("name") or "",
            "description_html": html.unescape(j.get("content") or ""),
            "posted_at": j.get("first_published") or j.get("updated_at"),
        }
        for j in data.get("jobs", [])
    ]


def _lever(key):
    region, company = key.split(":", 1)
    base = "https://api.eu.lever.co" if region == "eu" else "https://api.lever.co"
    data = get("%s/v0/postings/%s?mode=json" % (base, company)).json()
    out = []
    for j in data:
        body = j.get("description") or ""
        for lst in j.get("lists") or []:
            body += "<h3>%s</h3><ul>%s</ul>" % (lst.get("text", ""), lst.get("content", ""))
        body += j.get("additional") or ""
        out.append(
            {
                "title": j["text"].strip(),
                "url": j["hostedUrl"],
                "location": (j.get("categories") or {}).get("location") or "",
                "description_html": body,
            }
        )
    return out


def _workable(company):
    data = get("https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % company).json()
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join([x for x in (j.get("city"), j.get("country")) if x])
        out.append(
            {
                "title": j["title"].strip(),
                "url": j.get("url") or "https://apply.workable.com/%s/j/%s/" % (company, j["shortcode"]),
                "location": loc,
                "remote": bool(j.get("telecommuting")),
                "description_html": j.get("description") or "",
                "posted_at": j.get("published_on"),
            }
        )
    return out


def _personio(host):
    root = ET.fromstring(get("https://%s/xml?language=en" % host).content)
    out = []
    for p in root.iter("position"):
        body = ""
        for d in p.iter("jobDescription"):
            body += "<h3>%s</h3>%s" % ((d.findtext("name") or "").strip(), (d.findtext("value") or "").strip())
        out.append(
            {
                "title": (p.findtext("name") or "").strip(),
                "url": "https://%s/job/%s" % (host, p.findtext("id")),
                "location": (p.findtext("office") or "").strip(),
                "description_html": body,
                "posted_at": p.findtext("createdAt"),
                "subcompany": (p.findtext("subcompany") or "").strip(),
            }
        )
    return out


def _recruitee(company):
    data = get("https://%s.recruitee.com/api/offers/" % company).json()
    return [
        {
            "title": j["title"].strip(),
            "url": j["careers_url"],
            "location": j.get("location") or "",
            "remote": bool(j.get("remote")),
            "description_html": (j.get("description") or "") + (j.get("requirements") or ""),
            "posted_at": j.get("published_at"),
        }
        for j in data.get("offers", [])
    ]


def _ashby(company):
    data = get("https://api.ashbyhq.com/posting-api/job-board/%s" % company).json()
    return [
        {
            "title": j["title"].strip(),
            "url": j["jobUrl"],
            "location": j.get("location") or "",
            "remote": bool(j.get("isRemote")),
            "description_html": j.get("descriptionHtml") or "",
            "posted_at": j.get("publishedAt"),
        }
        for j in data.get("jobs", [])
        if j.get("isListed", True)
    ]


def _smartrecruiters(company):
    data = get("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100" % company).json()
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append(
            {
                "title": j["name"].strip(),
                "url": "https://jobs.smartrecruiters.com/%s/%s" % (company, j["id"]),
                "location": ", ".join([x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x]),
                "remote": bool(loc.get("remote")),
                "detail": {"kind": "smartrecruiters", "api": j.get("ref")},
                "posted_at": j.get("releasedDate"),
            }
        )
    return out


def _teamtailor(host):
    root = ET.fromstring(get("https://%s/jobs.rss" % host).content)
    out = []
    for it in root.iter("item"):
        locs = [e.text for e in it.iter() if e.tag.endswith("city") and e.text]
        out.append(
            {
                "title": (it.findtext("title") or "").strip(),
                "url": (it.findtext("link") or "").strip(),
                "location": ", ".join(dict.fromkeys(locs)),
                "description_html": html.unescape(it.findtext("description") or ""),
                "posted_at": it.findtext("pubDate"),
            }
        )
    return out


def _workday(key):
    host, tenant, site, query = key.split("|", 3)
    base = "https://%s/wday/cxs/%s/%s" % (host, tenant, site)
    facets = {k: v for k, v in parse_qs(query).items() if k not in ("q", "source")}
    search = (parse_qs(query).get("q") or [""])[0]
    out, offset, total = [], 0, None
    while offset < MAX_JOBS_PER_BOARD:
        data = post_json(base + "/jobs", {"limit": 20, "offset": offset, "searchText": search, "appliedFacets": facets}).json()
        posts = data.get("jobPostings") or []
        for j in posts:
            if not j.get("externalPath"):
                continue
            public = "https://%s/%s%s" % (host, site, j["externalPath"])
            if host.endswith("myworkdaysite.com"):
                public = "https://%s/recruiting/%s/%s%s" % (host, tenant, site, j["externalPath"])
            out.append(
                {
                    "title": (j.get("title") or "").strip(),
                    "url": public,
                    "location": j.get("locationsText") or "",
                    "detail": {"kind": "workday", "api": base + j["externalPath"]},
                }
            )
        # Workday geeft het totaal alleen op de eerste pagina betrouwbaar terug.
        if total is None:
            total = data.get("total") or 0
        offset += 20
        if len(posts) < 20 or offset >= total:
            break
    return out


def fetch_detail(detail):
    """Haalt de vacaturetekst op voor systemen die die niet in de lijst meegeven."""
    if detail["kind"] == "workday":
        info = get(detail["api"], headers={"Accept": "application/json"}).json().get("jobPostingInfo") or {}
        return info.get("jobDescription") or ""
    if detail["kind"] == "smartrecruiters":
        sections = (get(detail["api"]).json().get("jobAd") or {}).get("sections") or {}
        return "".join(
            "<h3>%s</h3>%s" % (s.get("title", ""), s.get("text", "")) for s in sections.values() if isinstance(s, dict)
        )
    return ""
