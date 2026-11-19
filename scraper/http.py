"""HTTP-hulpfuncties: ophalen van pagina's en omzetten naar compacte tekst met links."""
import re
import threading
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Een gewone browser-kenmerk: veel sites weigeren alles wat zich als script aandient.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = (10, 25)   # verbinden, lezen
_browsers = threading.Semaphore(3)   # een browser is zwaar: hooguit drie tegelijk
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en,nl;q=0.8,de;q=0.7",
                         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})


def _request(method, url, **kw):
    """Eén verzoek, met een paar herkansingen als de site vraagt om rustiger aan te doen of even hapert."""
    for attempt in range(3):
        try:
            r = _session.request(method, url, timeout=TIMEOUT, **kw)
        except (requests.ConnectionError, requests.Timeout):
            if attempt >= 1:   # onbereikbare sites kosten anders minuten per stuk
                raise
            time.sleep(3)
            continue
        if r.status_code in (429, 502, 503, 504) and attempt < 2:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(int(wait), 20) if wait.isdigit() else 5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r


def get(url, headers=None):
    return _request("GET", url, headers=headers or {})


def post_json(url, payload):
    return _request("POST", url, json=payload, headers={"Accept": "application/json"})


def html_to_text(html_doc, base_url, keep_links=True):
    """Zet HTML om naar leesbare tekst; links worden [tekst](absolute url)."""
    soup = BeautifulSoup(html_doc, "html.parser")
    # Ingesloten vacaturelijsten (iframe) blijven als link zichtbaar, zodat ze gevolgd kunnen worden.
    for fr in soup.find_all("iframe", src=True):
        if fr["src"].strip().startswith(("http", "/")):
            fr.replace_with(" [embedded frame](%s) " % urljoin(base_url, fr["src"].strip()))
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
    with _browsers, sync_playwright() as p:
        browser = p.chromium.launch(timeout=30000)
        try:
            page = browser.new_page(user_agent=UA, locale="en-US")
            page.set_default_timeout(10000)
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:  # sommige sites worden nooit "stil"; dan volstaat wat er na een tijdje staat
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
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


def fetch_page_text(url, min_chars=600, browser=False):
    """Geeft (tekst, manier). Valt terug op een browser als de kale HTML weinig tekst bevat,
    of gebruikt die meteen als browser=True."""
    text, err = "", None
    if not browser:
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
