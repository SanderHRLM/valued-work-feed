"""HTTP-hulpfuncties: ophalen van pagina's en omzetten naar compacte tekst met links."""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; valued.work-jobs/1.0; +https://www.valued.work)"
TIMEOUT = 30
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en,nl;q=0.8,de;q=0.7"})


def get(url, headers=None):
    r = _session.get(url, timeout=TIMEOUT, headers=headers or {})
    r.raise_for_status()
    return r


def post_json(url, payload):
    r = _session.post(url, json=payload, timeout=TIMEOUT, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r


def html_to_text(html_doc, base_url, keep_links=True):
    """Zet HTML om naar leesbare tekst; links worden [tekst](absolute url)."""
    soup = BeautifulSoup(html_doc, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe", "head"]):
        tag.decompose()
    if keep_links:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            label = a.get_text(" ", strip=True)
            if label:
                a.replace_with(" [%s](%s) " % (label, urljoin(base_url, href)))
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _render(url):
    """Laadt de pagina in een echte browser, voor sites die hun vacatures met JavaScript opbouwen."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(1500)
            frames = [page.content()]
            for fr in page.frames[1:]:
                try:
                    frames.append(fr.content())
                except Exception:
                    pass
            return "\n".join(frames)
        finally:
            browser.close()


def fetch_page_text(url, min_chars=600):
    """Geeft (tekst, manier). Valt terug op een browser als de kale HTML weinig tekst bevat."""
    text, err = "", None
    try:
        text = html_to_text(get(url).text, url)
    except Exception as e:  # noqa: BLE001 - elke fout hier betekent: probeer de browser
        err = e
    if len(text) >= min_chars:
        return text, "http"
    try:
        rendered = _render(url)
    except Exception as e:  # noqa: BLE001
        rendered, err = None, err or e
    if rendered:
        rtext = html_to_text(rendered, url)
        if len(rtext) > len(text):
            return rtext, "browser"
    if not text and err:
        raise err
    return text, "http"
