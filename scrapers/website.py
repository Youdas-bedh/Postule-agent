"""Polite, bounded same-domain static HTML scraper for company public pages."""
from __future__ import annotations
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup
from utils.urls import is_public_web_domain, normalize_domain, normalize_url

logger = logging.getLogger(__name__)
USER_AGENT = "PostuleAgentCompanyResearch/1.0 (+local research; contact: configured by operator)"
PRIORITY = ("about", "about-us", "company", "who-we-are", "solutions", "products", "services", "technology", "ai", "data", "innovation", "a-propos", "qui-sommes-nous", "nos-activites", "expertises", "technologies")

@dataclass
class PageContent:
    url: str
    title: str = ""
    meta_description: str = ""
    headings: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    text: str = ""

@dataclass
class WebsiteResult:
    website_url: str
    pages: list[PageContent]
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def clean_html(html: str, url: str = "https://example.com/") -> PageContent:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script,style,noscript,svg,iframe,nav,footer,header,form,button,[hidden],[aria-hidden='true']"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if not getattr(node, "attrs", None):
            continue
        labels = " ".join([str(node.get("id", "")), " ".join(map(str, node.get("class", []))) ]).casefold()
        if any(token in labels for token in ("cookie", "consent", "navigation", "mega-menu", "site-menu")):
            node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    description = str(meta.get("content", "")).strip() if meta else ""
    headings = [node.get_text(" ", strip=True) for node in soup.select("h1,h2,h3")]
    paragraphs = [node.get_text(" ", strip=True) for node in soup.select("main p, article p, p")]
    paragraphs = list(dict.fromkeys(p for p in paragraphs if len(p) >= 45))
    text = "\n".join(([f"Title: {title}"] if title else []) + ([f"Description: {description}"] if description else []) + [f"Heading: {h}" for h in headings] + paragraphs)
    return PageContent(url, title[:300], description[:1000], headings[:35], paragraphs[:80], text[:12000])


def select_relevant_pages(home_url: str, html: str, max_pages: int = 8) -> list[str]:
    root = normalize_domain(home_url)
    found: dict[str, tuple[int, str]] = {}
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = normalize_url(urljoin(home_url, a["href"]))
        if not href or normalize_domain(href) != root:
            continue
        path = urlsplit(href).path.lower().strip("/")
        if not path or path.count("/") > 2:
            continue
        label = (a.get_text(" ", strip=True) + " " + path).lower()
        rank = next((idx for idx, word in enumerate(PRIORITY) if word in label), len(PRIORITY) + 1)
        if rank <= len(PRIORITY):
            found[href] = (rank, href)
    for index, slug in enumerate(PRIORITY):
        href = normalize_url(urljoin(home_url.rstrip("/") + "/", slug))
        found.setdefault(href, (index, href))
    return [url for url, _ in sorted(found.items(), key=lambda item: (item[1][0], item[0]))[:max(0, max_pages - 1)]]


class WebsiteScraper:
    def __init__(self, timeout: float = 12, max_pages: int = 8, min_interval: float = 0.25, concurrency: int = 3):
        self.timeout, self.max_pages = timeout, max(1, min(max_pages, 10))
        self.min_interval, self.concurrency = min_interval, max(1, min(concurrency, 3))
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._allowed_domain: str | None = None

    def _get(self, client: httpx.Client, url: str) -> httpx.Response:
        current = url
        for _ in range(6):
            with self._lock:
                delay = self.min_interval - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                self._last_request = time.monotonic()
            logger.info("[SCRAPER] GET %s", urlsplit(current).netloc)
            response = client.get(current, follow_redirects=False)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            destination = urljoin(str(response.url), location or "")
            if not location or normalize_domain(destination) != self._allowed_domain:
                raise RuntimeError("Redirection vers un domaine externe refusée")
            current = destination
        raise RuntimeError("Trop de redirections")

    def scrape(self, website_url: str) -> WebsiteResult:
        started = time.perf_counter()
        normalized = normalize_url(website_url)
        if not normalized:
            raise ValueError("URL du site invalide")
        if urlsplit(normalized).scheme != "https":
            normalized = "https://" + normalize_domain(normalized) + "/"
        self._allowed_domain = normalize_domain(normalized)
        if not is_public_web_domain(self._allowed_domain):
            raise ValueError("Les hôtes locaux et adresses IP privées ne sont pas autorisés")
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}) as client:
            try:
                response = self._get(client, normalized)
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "text/html").lower():
                    raise ValueError("Le site ne renvoie pas du HTML")
            except (httpx.HTTPError, ValueError) as exc:
                raise RuntimeError(f"Site inaccessible ou non HTML ({type(exc).__name__})") from exc
            home_url = str(response.url)
            root = normalize_domain(home_url)
            pages = [clean_html(response.text, home_url)]
            target_urls = [u for u in select_relevant_pages(home_url, response.text, self.max_pages) if normalize_domain(u) == root]
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                futures = {pool.submit(self._get, client, url): url for url in target_urls}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        page_response = future.result()
                        page_response.raise_for_status()
                        if normalize_domain(str(page_response.url)) != root:
                            continue
                        if "html" in page_response.headers.get("content-type", "text/html").lower():
                            pages.append(clean_html(page_response.text, str(page_response.url)))
                    except (httpx.HTTPError, RuntimeError) as exc:
                        errors.append(f"{url}: {type(exc).__name__}")
                        logger.info("[SCRAPER] Skipped blocked page host=%s (%s)", root, type(exc).__name__)
        # Unique URLs and bounded prompt material.
        unique = list({p.url: p for p in pages}.values())[:self.max_pages]
        logger.info("[SCRAPER] Found %d relevant pages", len(unique))
        return WebsiteResult(str(response.url), unique, errors, time.perf_counter() - started)
