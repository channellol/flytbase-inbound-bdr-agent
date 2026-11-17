"""Web access layer: search, fetch, crawl helpers.

Every network call goes through here so that caching, retries and backoff
are applied the same way everywhere.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

from core.log import log
import logging, warnings
from bs4 import XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
logging.getLogger("pypdf").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_PDF_PAGES = 300


def _key(prefix: str, s: str) -> Path:
    return CACHE_DIR / f"{prefix}_{hashlib.sha1(s.encode()).hexdigest()}.json"


def _cache_get(path: Path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _cache_put(path: Path, data) -> None:
    path.write_text(json.dumps(data))


def backoff_sleep(attempt: int, base: float = 2.0, cap: float = 60.0) -> None:
    time.sleep(min(cap, base * (2 ** attempt)) + random.uniform(0, 1))


# --------------------------------------------------------------------- fetch
def _html_to_text(html: str) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        tag.decompose()
    # animated stat counters start at "0" in the HTML and are filled by JavaScript; drop the fake zero
    for tag in soup.select(".counter"):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return title, text, links


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages):
        if i >= MAX_PDF_PAGES:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return re.sub(r"\s+", " ", " ".join(parts))


_dead_hosts: dict[str, int] = {}  # host -> timeouts/blocks this run; a host that fails twice is skipped


def fetch(url: str, use_cache: bool = True, retries: int = 4) -> dict:
    """Fetch a URL and return {url, final_url, ok, status, title, text, links, kind}."""
    url = urldefrag(url)[0]
    path = _key("page", url)
    if use_cache:
        hit = _cache_get(path)
        if hit is not None:
            return hit
    result = {"url": url, "final_url": url, "ok": False, "status": None,
              "title": "", "text": "", "links": [], "kind": "", "error": ""}
    host = domain(url)
    if _dead_hosts.get(host, 0) >= 2:
        result["error"] = "host skipped: timed out or blocked earlier in this run"
        return result
    for attempt in range(retries):
        try:
            # sec.gov requires a declared research user agent (its fair-access policy)
            ua = "flytbase-bdr-prototype research@example.com" if domain(url).endswith("sec.gov") else UA
            r = requests.get(url, headers={"User-Agent": ua, "Accept-Language": "en,es;q=0.8"},
                             timeout=(10, 25), allow_redirects=True)
            result["status"] = r.status_code
            result["final_url"] = r.url
            if r.status_code in (429, 500, 502, 503, 504):
                log(f"fetch {url} -> HTTP {r.status_code}, retrying", level="warn")
                backoff_sleep(attempt)
                continue
            if r.status_code >= 400:
                result["error"] = f"HTTP {r.status_code}"
                if r.status_code in (401, 403):
                    _dead_hosts[host] = _dead_hosts.get(host, 0) + 1
                break
            ctype = r.headers.get("content-type", "").lower()
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                result["kind"] = "pdf"
                result["text"] = _pdf_to_text(r.content)
                result["title"] = url.rsplit("/", 1)[-1]
            else:
                result["kind"] = "html"
                title, text, links = _html_to_text(r.text)
                result.update(title=title, text=text,
                              links=sorted({urldefrag(urljoin(r.url, l))[0] for l in links}))
            result["ok"] = bool(result["text"])
            if not result["ok"]:
                result["error"] = "empty text"
            break
        except Exception as e:  # network errors, PDF parse errors
            result["error"] = f"{type(e).__name__}: {e}"[:300]
            log(f"fetch {url} failed ({result['error'][:80]}), attempt {attempt + 1}", level="warn")
            if "Timeout" in type(e).__name__:  # slow or silently blocking host: do not keep waiting on it
                _dead_hosts[host] = _dead_hosts.get(host, 0) + 1
                break
            backoff_sleep(attempt)
    if result["ok"]:
        _cache_put(path, result)
    return result


def fetch_xml_locs(url: str) -> list[str]:
    """Return <loc> entries from a sitemap (cached)."""
    path = _key("sitemap", url)
    hit = _cache_get(path)
    if hit is not None:
        return hit
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200:
                locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
                _cache_put(path, locs)
                return locs
            backoff_sleep(attempt)
        except Exception:
            backoff_sleep(attempt)
    return []


# -------------------------------------------------------------------- search
def _google_apify(query: str, max_results: int) -> list[dict] | None:
    """Google results via Apify's Google Search Scraper (needs APIFY_TOKEN). None if unavailable."""
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return None
    try:
        r = requests.post("https://api.apify.com/v2/acts/apify~google-search-scraper/run-sync-get-dataset-items",
                          params={"token": token}, timeout=120,
                          json={"queries": query, "maxPagesPerQuery": 1, "resultsPerPage": max(10, max_results)})
        r.raise_for_status()
        out = []
        for item in r.json():
            for x in item.get("organicResults", []):
                if x.get("url"):
                    out.append({"title": x.get("title", ""), "url": x["url"], "snippet": x.get("description", "")})
        return out[:max_results]
    except Exception as e:
        log(f"google search via Apify failed ({type(e).__name__}), using DuckDuckGo", level="warn")
        return None


def search(query: str, max_results: int = 8) -> list[dict]:
    """Web search: Google via Apify when APIFY_TOKEN is set, otherwise free DuckDuckGo (ddgs)."""
    path = _key("search", f"{query}|{max_results}|{'g' if os.environ.get('APIFY_TOKEN') else 'd'}")
    hit = _cache_get(path)
    if hit is not None:
        return hit
    g = _google_apify(query, max_results)
    if g:
        _cache_put(path, g)
        return g
    from ddgs import DDGS

    for attempt in range(5):
        try:
            raw = DDGS().text(query, max_results=max_results) or []
            out = [{"title": x.get("title", ""), "url": x.get("href", ""),
                    "snippet": x.get("body", "")} for x in raw if x.get("href")]
            _cache_put(path, out)
            time.sleep(1.5)  # be polite to the free endpoint
            return out
        except Exception as e:
            log(f"search '{query}' failed ({type(e).__name__}), attempt {attempt + 1}", level="warn")
            backoff_sleep(attempt, base=3)
    return []


# ------------------------------------------------------------------- helpers
def domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def norm(s: str) -> str:
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace(" ", " ")
    s = re.sub(r"[^a-z0-9$%.,' -]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """Programmatic evidence check: is the quoted evidence actually on the page?"""
    q, t = norm(quote), norm(text)
    if len(q) < 12:
        return False
    if q in t:
        return True
    # tolerate small edits: accept if a long contiguous 10-word window matches
    words = q.split()
    if len(words) >= 10:
        for i in range(0, len(words) - 9):
            if " ".join(words[i:i + 10]) in t:
                return True
    return False


def relevant_excerpt(text: str, keywords: list[str], max_chars: int = 12000, window: int = 900) -> str:
    """Pick the most keyword-dense windows of a long document, kept in original order."""
    if len(text) <= max_chars:
        return text
    kws = [k.lower() for k in keywords]
    chunks = [text[i:i + window] for i in range(0, len(text), window)]
    scored = []
    for idx, c in enumerate(chunks):
        lc = c.lower()
        scored.append((sum(lc.count(k) for k in kws), idx))
    keep = sorted(i for s, i in sorted(scored, reverse=True)[: max_chars // window] if s > 0)
    if not keep:
        return text[:max_chars]
    return " [...] ".join(chunks[i] for i in keep)
