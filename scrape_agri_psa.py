#!/usr/bin/env python3
"""Build an auditable English-only Kenyan agriculture PSA corpus from licensed pages.

The program intentionally collects only sources configured with explicit re-use terms,
obeys robots.txt, throttles requests, and writes paragraph-level records rather than
silently treating an entire web page as one translation unit.

Changes from the bilingual version:
  * English-only: all Swahili term lists, detection branches, and the hreflang
    "alternate language" crawl/parallel-pairing logic have been removed. Fewer
    regex branches to test per block AND fewer pages fetched per source (no more
    detour to fetch the sw/ counterpart of every en/ page), so it's faster too.
  * Record + time budgeted: --target-records and --time-budget-minutes let the
    run stop as soon as it has "enough", instead of only stopping when every
    source's --max-pages-per-source is exhausted.
  * Bandwidth-capped fetches: HTML responses are read up to --max-page-bytes
    (default 2 MB) instead of being downloaded in full; large listing/archive
    pages you'd only skim three paragraphs from no longer cost their whole size.
  * --max-workers is now explicit and defaults higher (up to 8), since dropping
    the alternate-language crawl frees up headroom without hammering any single host
    harder (the per-host --delay is unchanged and still enforced).
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import logging
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# Parallel-corpus fields (Language, Language_Code, Parallel_Group_ID, Parallel_Link_URL)
# are gone: every record is English, and we no longer chase a Swahili counterpart page.
SCHEMA = [
    "ID", "Domain", "PSA", "PSA_Type",
    "Source_URL", "Source_Title", "Publisher", "Organization_Type",
    "Published_Date", "Licence", "Licence_URL", "Licence_Evidence",
    "Collected_At", "Review_Status",
]

AGRI_TERMS = (
    "agricultur", "farmer", "farm", "crop", "maize", "bean", "wheat", "rice", "sorghum",
    "millet", "cassava", "potato", "vegetable", "fruit", "horticulture", "livestock", "cattle",
    "dairy", "poultry", "chicken", "goat", "sheep", "fodder", "feed", "fertilizer", "seed",
    "soil", "harvest", "planting", "irrigation", "rainfall", "drought", "pest", "disease",
    "fall armyworm", "market price", "food security", "agronom", "agroecolog", "agroforestry",
    "beekeep", "coffee", "tea", "sugarcane", "fisher", "aquaculture", "organic farming",
)
PSA_SIGNALS = (
    "advisory", "alert", "notice", "announcement", "update", "warning", "guidance", "recommend",
    "farmers should", "must", "how to", "tips", "market", "forecast", "extension", "control", "manage",
    "prevent", "protect", "prepare", "register", "apply", "training", "call for", "public participation",
)
KENYA_TERMS = ("kenya", "kenyan", "nairobi", "mombasa", "kisumu", "nakuru", "eldoret", "kiambu", "makueni", "kajiado", "turkana")
SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".mp3", ".mp4", ".avi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf")
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi"}

IMPERATIVE_STARTERS = [
    "activate", "apply", "attend", "avoid", "check", "clean", "collect", "confirm",
    "cooperate", "create", "destroy", "download", "ensure", "heed", "inspect",
    "log in", "login", "maintain", "manage", "minimize", "monitor", "note", "observe", "obtain",
    "participate", "pay", "prepare", "prevent", "protect", "read", "register",
    "renew", "report", "review", "sanitize", "say no", "select", "stay clear", "store",
    "submit", "track", "transfer", "update", "upload", "use", "verify", "visit",
    "plant", "sow", "harvest", "prune", "weed", "water", "irrigate", "fertilize", "feed",
    "vaccinate", "drench", "treat", "spray", "mulch", "plough", "till", "graze", "breed",
    "cull", "store", "dry", "grade", "sell", "market", "isolate", "quarantine", "dip",
    "all farmers", "all pastoralists", "all growers", "all breeders", "notice is hereby given",
    "the ministry of agriculture", "county government", "extension officer",
    "farmers are advised", "farmers should", "pastoralists should", "growers should",
    "always", "never", "do not", "don't", "to prevent", "to control", "to manage", "to avoid",
]

MODAL_ACTION_PATTERNS = [
    r"\b(farmers?|pastoralists?|growers?|breeders?|producers?)\s+(should|must|are advised|need to)\b",
    r"\b(should|must) (be|have|apply|plant|spray|harvest|vaccinate|control|prevent|manage|verify|check)\b",
    r"\b(is|are) recommended\b",
]

REJECT_PATTERNS = [
    r"\b(is a channel of|provides scientific and practical)\b",
    r"\b(magazine with practical information|publications provide a range of)\b",
    r"\b(cookie|privacy policy|terms of use|all rights reserved|javascript|browser)\b",
]

# Pre-compiled regexes for performance — built once at import time, never rebuilt.
_RE_WHITESPACE = re.compile(r"\s+")
_RE_DOUBLE_SLASH = re.compile(r"/{2,}")
_RE_WORD = re.compile(r"[a-zA-Z\xc0-\xff']+")
_RE_WORDS_HYPHENS = re.compile(r"\b[\w'-]+\b")
_RE_URL = re.compile(r"(?:https?://|www\.)")
_RE_NON_WORD = re.compile(r"[\W\d_]+")
_RE_SENT_SPLIT = re.compile(r"[.!?]\s+")
_RE_PREFIX_STRIP = re.compile(r"^[\w\s\(\)\/]+:\s*")
_RE_MODAL = re.compile("|".join(MODAL_ACTION_PATTERNS), re.I)
_RE_REJECT = re.compile("|".join(REJECT_PATTERNS), re.I)
# Single alternation pattern: matches any imperative starter at sentence start.
_RE_STARTERS = re.compile(
    r"^(?:" + "|".join(re.escape(s) for s in sorted(IMPERATIVE_STARTERS, key=len, reverse=True)) + r")(?:\s|,|$)",
    re.I,
)
_RE_URL_CHECK = re.compile(r"(?:https?://|www\.)")
_RE_EN_DETECT = re.compile(r"\b(the|and|with|from|farmers?)\b")
_RE_MAIN_CLASS = re.compile(r"(content|entry|article|post|main)", re.I)

EN_MARKERS = {"the", "and", "of", "to", "in", "for", "with", "farmers", "agriculture", "crop", "livestock", "weather", "market"}
# Any of these lang-prefix values on <html lang="..."> lets us reject a page
# before we even look at its words — cheaper than running the heuristic.
NON_ENGLISH_LANG_PREFIXES = {"sw", "fr", "ar", "am", "so", "zh", "es", "pt", "de"}


def clean_text_prefix(text: str) -> str:
    return _RE_PREFIX_STRIP.sub("", text).strip()


@dataclass
class RunStats:
    pages_seen: int = 0
    pages_fetched: int = 0
    records_written: int = 0
    duplicate_records: int = 0
    robots_skipped: int = 0
    licence_skipped: int = 0
    relevance_skipped: int = 0
    non_english_skipped: int = 0
    errors: int = 0


class Progress:
    """Shared, thread-safe stop condition: whichever of (record target, time
    budget) is hit first ends the crawl. Checked cooperatively by every
    source's crawl loop so a big pilot doesn't overrun once you have enough."""

    def __init__(self, target_records: int, deadline_monotonic: float):
        self.target_records = target_records
        self.deadline_monotonic = deadline_monotonic
        self._count = 0
        self._lock = threading.Lock()

    def add(self, n: int = 1) -> None:
        with self._lock:
            self._count += n

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def done(self) -> bool:
        return self.count >= self.target_records or time.monotonic() >= self.deadline_monotonic


def collapse(value: str) -> str:
    return _RE_WHITESPACE.sub(" ", value.replace("\xa0", " ")).strip()


def normalise_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
    path = _RE_DOUBLE_SLASH.sub("/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query, doseq=True), ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().split(":")[0]


def text_hash(text: str) -> str:
    return hashlib.sha256(collapse(text).casefold().encode("utf-8")).hexdigest()


def record_id(url: str, psa: str) -> str:
    digest = hashlib.sha256(f"{normalise_url(url)}\0{collapse(psa)}".encode("utf-8")).hexdigest()[:18].upper()
    return f"KAPSA-{digest}"


def windows_trust_bundle() -> Path:
    """Create a PEM bundle from the Windows trusted-root stores.

    Some managed Windows networks intercept HTTPS with a certificate trusted by
    Windows but unavailable to an MSYS/Python certifi bundle. This preserves TLS
    verification by using Windows' trust decision; it never disables verification.
    """
    if sys.platform != "win32":
        raise RuntimeError("--use-windows-root-certificates is available on Windows only")
    command = (
        "$stores = @('Cert:\\CurrentUser\\Root', 'Cert:\\LocalMachine\\Root'); "
        "foreach ($store in $stores) { if (Test-Path $store) { "
        "Get-ChildItem $store | ForEach-Object { [Convert]::ToBase64String($_.RawData) } } }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not read Windows trusted roots: {result.stderr.strip()}")
    certificates = []
    for line in result.stdout.splitlines():
        try:
            certificates.append(ssl.DER_cert_to_PEM_cert(base64.b64decode(line.strip(), validate=True)))
        except Exception:
            continue
    if not certificates:
        raise RuntimeError("Windows trusted-root stores returned no usable certificates")
    bundle = Path(tempfile.gettempdir()) / "kenya-agri-psa-windows-trusted-roots.pem"
    bundle.write_text("".join(dict.fromkeys(certificates)), encoding="ascii")
    return bundle


def is_english_text(text: str, page_lang: str = "") -> bool:
    """Conservative English detector. A declared non-English <html lang> short-circuits
    immediately (cheap rejection before any regex runs); otherwise falls back to a
    word-frequency heuristic."""
    prefix = (page_lang or "").lower().split("-")[0]
    if prefix == "en":
        return True
    if prefix in NON_ENGLISH_LANG_PREFIXES:
        return False
    lowered = text.casefold()
    words = _RE_WORD.findall(lowered)
    if not words:
        return False
    en_score = sum(word in EN_MARKERS for word in words)
    return en_score >= 2 or bool(_RE_EN_DETECT.search(lowered))


def has_term(text: str, terms: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in terms)


def classify_psa(text: str) -> str:
    lowered = text.casefold()
    if any(term in lowered for term in ("pest", "disease", "armyworm")):
        return "Pest or disease advisory"
    if any(term in lowered for term in ("weather", "rainfall", "drought", "forecast")):
        return "Weather or climate notice"
    if any(term in lowered for term in ("market", "price", "subsid", "grant")):
        return "Market or support update"
    if any(term in lowered for term in ("notice", "announcement", "public participation")):
        return "Public announcement"
    return "Agricultural extension guidance"


def is_relevant(title: str, page_text: str, source: dict[str, Any], strict_psa: bool) -> bool:
    combined = f"{title} {page_text}".casefold()
    if not has_term(combined, AGRI_TERMS):
        return False
    if source.get("kenya_context_required") and not has_term(combined, KENYA_TERMS):
        return False
    if strict_psa:
        return has_term(combined, PSA_SIGNALS)
    return True


def is_usable_text_block(block: str) -> bool:
    """Check if statement is an agricultural PSA starting directly with directive action."""
    if len(block) > 900:
        return False
    lowered = block.casefold()
    if len(_RE_WORDS_HYPHENS.findall(lowered)) < 5:
        return False
    if _RE_URL_CHECK.search(lowered) or "isbn" in lowered or "©" in block:
        return False
    if lowered.startswith(("reference", "references", "bibliography", "source:", "photo:", "figure ")):
        return False
    if lowered.count("(revised)") >= 3 or (lowered.count(" crops ") >= 5 and lowered.count(" pest") >= 3):
        return False
    if _RE_REJECT.search(lowered):
        return False

    cleaned = clean_text_prefix(lowered)
    for sentence in _RE_SENT_SPLIT.split(cleaned):
        sentence = sentence.strip()
        if not sentence:
            continue
        s_clean = clean_text_prefix(sentence)
        if _RE_STARTERS.match(s_clean) or _RE_MODAL.search(sentence):
            return True

    return False


def extract_page(html: str | bytes, url: str) -> tuple[dict[str, Any], Any]:
    """Parse page HTML and return (page_data, soup). Soup is returned so the
    caller can reuse it for link discovery without parsing the HTML a second time."""
    soup = BeautifulSoup(html, "html.parser")
    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    canonical = normalise_url(urljoin(url, canonical_tag.get("href"))) if canonical_tag and canonical_tag.get("href") else normalise_url(url)
    title = ""
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        title = collapse(title_tag.get_text(" ", strip=True))
    for element in soup(["script", "style", "noscript", "svg", "canvas", "nav", "header", "footer", "aside", "form", "iframe"]):
        element.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    if main is None:
        main = soup.find(class_=_RE_MAIN_CLASS) or soup.body or soup
    blocks: list[str] = []
    for tag in main.find_all(["p", "li", "h2", "h3", "h4"]):
        block = collapse(tag.get_text(" ", strip=True))
        if 70 <= len(block) <= 1800 and not _RE_NON_WORD.fullmatch(block) and is_usable_text_block(block):
            blocks.append(block)
    if not blocks:
        plain = collapse(main.get_text(" ", strip=True))
        blocks = [plain] if 100 <= len(plain) <= 900 and is_usable_text_block(plain) else []
    body_text = collapse(" ".join(blocks))
    page_lang = (soup.html.get("lang", "") if soup.html else "")
    published = ""
    time_tag = soup.find("time")
    if time_tag:
        published = collapse(time_tag.get("datetime") or time_tag.get_text(" ", strip=True))
    if not published:
        for prop in ("article:published_time", "date", "dc.date", "DC.date"):
            meta = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if meta and meta.get("content"):
                published = collapse(meta["content"])
                break
    return (
        {"canonical": canonical, "title": title, "blocks": blocks, "body_text": body_text,
         "page_lang": page_lang, "published": published,
         "full_text": collapse(soup.get_text(" ", strip=True))},
        soup,
    )


def licence_evidence(page_text: str, source: dict[str, Any]) -> str:
    if source["license_mode"] == "sitewide":
        return f"Site-wide terms verified at {source['license_url']}"
    lowered = page_text.casefold()
    keywords = [word.casefold() for word in source.get("license_keywords", [])]
    if not all(word in lowered for word in keywords[:2]):
        return ""
    pattern = re.compile(r".{0,90}(?:licensed under|licen[cs]e[ds]? under|this work is).{0,180}(?:creative commons|cc[-\s]?by).{0,90}", re.I)
    match = pattern.search(page_text)
    return collapse(match.group(0)) if match else ""


class LicensedCrawler:
    def __init__(self, args: argparse.Namespace, source: dict[str, Any], stats: RunStats, progress: Progress):
        self.args = args
        self.source = source
        self.stats = stats
        self.progress = progress
        self.session = requests.Session()
        self.session.max_redirects = 5
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=1)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": args.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        })
        if args.use_windows_root_certificates:
            bundle = windows_trust_bundle()
            self.session.verify = str(bundle)
            logging.info("Using Windows trusted-root bundle for TLS verification")
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def request(self, url: str, cap_bytes: int | None = None) -> requests.Response | None:
        host = host_of(url)
        elapsed = time.monotonic() - self.last_request.get(host, 0)
        if elapsed < self.args.delay:
            time.sleep(self.args.delay - elapsed)
        try:
            response = self.session.get(url, timeout=self.args.timeout, allow_redirects=True, stream=bool(cap_bytes))
            self.last_request[host] = time.monotonic()
            if cap_bytes and response.status_code == 200:
                content_type = response.headers.get("content-type", "").lower()
                if "html" in content_type:
                    # Read at most cap_bytes from the wire instead of the whole page —
                    # a long news-listing or archive page still gives us a usable DOM
                    # for the visible article/paragraph tags without downloading its
                    # full byte size. response.content / .text below just reuse this.
                    try:
                        data = response.raw.read(cap_bytes, decode_content=True)
                        response._content = data
                    except Exception:
                        pass
                    finally:
                        response.close()
                else:
                    response.close()
            return response
        except requests.RequestException as exc:
            logging.warning("Request failed: %s (%s)", url, exc)
            self.stats.errors += 1
            return None

    def load_robots(self, url: str) -> RobotFileParser | None:
        host = host_of(url)
        if host in self.robots:
            return self.robots[host]
        robots_url = f"{urlsplit(url).scheme}://{host}/robots.txt"
        response = self.request(robots_url)
        if response is None or response.status_code >= 400:
            logging.warning("Skipping %s: robots.txt unavailable (%s)", host, response.status_code if response else "request error")
            self.robots[host] = None  # type: ignore[assignment]
            return None
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        self.robots[host] = parser
        return parser

    def allowed_by_robots(self, url: str) -> bool:
        parser = self.load_robots(url)
        if parser is None or not parser.can_fetch(self.args.user_agent, url):
            self.stats.robots_skipped += 1
            return False
        return True

    def source_url_allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or host_of(url) not in set(self.source["allowed_domains"]):
            return False
        path = parsed.path.lower()
        if path.endswith(SKIP_SUFFIXES):
            return False
        if any(fragment.lower() in path for fragment in self.source.get("exclude_path_patterns", [])):
            return False
        patterns = self.source.get("include_path_patterns", [])
        return not patterns or any(fragment.lower() in path for fragment in patterns)

    def verify_sitewide_licence(self) -> bool:
        if self.source["license_mode"] != "sitewide":
            return True
        url = self.source["license_url"]
        if not self.allowed_by_robots(url):
            return False
        response = self.request(url)
        if response is None or response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
            logging.warning("Cannot verify licence for %s", self.source["id"])
            return False
        text = collapse(BeautifulSoup(response.content, "html.parser").get_text(" ", strip=True)).casefold()
        matches = all(keyword.casefold() in text for keyword in self.source.get("license_keywords", []))
        if not matches:
            logging.warning("Licence wording did not match source configuration for %s", self.source["id"])
        return matches

    def sitemap_urls(self, sitemap_url: str, seen: set[str], limit: int) -> list[str]:
        if sitemap_url in seen or len(seen) >= self.args.max_sitemaps:
            return []
        seen.add(sitemap_url)
        if not self.allowed_by_robots(sitemap_url):
            return []
        response = self.request(sitemap_url)
        if response is None or response.status_code != 200:
            return []
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            logging.warning("Invalid sitemap XML: %s", sitemap_url)
            return []
        locations = [collapse(elem.text or "") for elem in root.iter() if elem.tag.endswith("loc")]
        if root.tag.endswith("sitemapindex"):
            found: list[str] = []
            for location in locations:
                found.extend(self.sitemap_urls(normalise_url(location), seen, limit - len(found)))
                if len(found) >= limit:
                    break
            return found[:limit]
        return [normalise_url(location) for location in locations[:limit] if self.source_url_allowed(normalise_url(location))]

    def initial_queue(self) -> deque[str]:
        # Seed pages go first so a small pilot samples the intended content even
        # when a site exposes a very large, chronologically ordered sitemap.
        candidates: list[str] = [normalise_url(url) for url in self.source.get("seed_urls", [])]
        crawl_ceiling = max(self.args.max_pages_per_source * 6, self.args.max_pages_per_source)
        if self.args.max_sitemaps:
            for sitemap in self.source.get("sitemap_urls", []):
                candidates.extend(self.sitemap_urls(normalise_url(sitemap), set(), crawl_ceiling - len(candidates)))
                if len(candidates) >= crawl_ceiling:
                    break
        unique = list(dict.fromkeys(url for url in candidates if self.source_url_allowed(url)))
        return deque(unique[:crawl_ceiling])

    def crawl(self) -> Iterable[dict[str, str]]:
        if not self.verify_sitewide_licence():
            logging.warning("Source disabled because licence verification failed: %s", self.source["id"])
            return
        queue = self.initial_queue()
        visited: set[str] = set()
        while queue and self.stats.pages_fetched < self.args.max_pages_per_source:
            if self.progress.done():
                # Global record target or time budget reached — stop this source's
                # crawl even if its own per-source page cap hasn't been hit yet.
                break
            candidate = normalise_url(queue.popleft())
            if candidate in visited or not self.source_url_allowed(candidate):
                continue
            visited.add(candidate)
            self.stats.pages_seen += 1
            if not self.allowed_by_robots(candidate):
                continue
            response = self.request(candidate, cap_bytes=self.args.max_page_bytes)
            if response is None or response.status_code != 200:
                continue
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                continue
            self.stats.pages_fetched += 1
            page, soup = extract_page(response.content, response.url)
            if not is_english_text(page["body_text"] or page["title"], page["page_lang"]):
                self.stats.non_english_skipped += 1
            elif not is_relevant(page["title"], page["body_text"], self.source, self.args.strict_psa):
                self.stats.relevance_skipped += 1
            else:
                evidence = licence_evidence(page["full_text"], self.source)
                if self.source["license_mode"] == "per_page" and not evidence:
                    self.stats.licence_skipped += 1
                else:
                    for block in page["blocks"]:
                        if not is_english_text(block, page["page_lang"]):
                            continue
                        if not has_term(f"{page['title']} {block}", AGRI_TERMS):
                            continue
                        psa = block if len(block) > 130 or not page["title"] else f"{page['title']}: {block}"
                        self.progress.add(1)
                        yield {
                            "ID": record_id(page["canonical"], psa),
                            "Domain": host_of(page["canonical"]),
                            "PSA": psa,
                            "PSA_Type": classify_psa(f"{page['title']} {psa}"),
                            "Source_URL": page["canonical"],
                            "Source_Title": page["title"],
                            "Publisher": self.source["publisher"],
                            "Organization_Type": self.source["organization_type"],
                            "Published_Date": page["published"],
                            "Licence": self.source["license"],
                            "Licence_URL": self.source.get("license_url", "") or page["canonical"],
                            "Licence_Evidence": evidence,
                            "Collected_At": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                            "Review_Status": "candidate — provenance and licence verified; review PSA scope and alignment before model training",
                        }
            queue_limit = self.args.max_pages_per_source * 8
            for link in soup.find_all("a", href=True):
                target = normalise_url(urljoin(response.url, link["href"]))
                if target not in visited and self.source_url_allowed(target) and len(queue) < queue_limit:
                    queue.appendleft(target)


def read_sources(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload["sources"]


def load_existing_ids(path: Path) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    ids, hashes = set(), set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("ID"):
                ids.add(row["ID"])
            if row.get("PSA"):
                hashes.add(text_hash(row["PSA"]))
    return ids, hashes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", default="sources.json", type=Path)
    parser.add_argument("--output", default="data/kenya_agri_psa.csv", type=Path)
    parser.add_argument("--report", default="data/collection_report.json", type=Path)
    parser.add_argument("--max-pages-per-source", type=int, default=400, help="Per-source ceiling; the global --target-records / --time-budget-minutes usually stop a run earlier than this.")
    parser.add_argument("--target-records", type=int, default=1200, help="Stop once roughly this many candidate records have been yielded across all sources (some buffer above 1000 to absorb de-duplication).")
    parser.add_argument("--time-budget-minutes", type=float, default=20.0, help="Hard wall-clock cap for the whole run, regardless of per-source/global-record settings.")
    parser.add_argument("--max-page-bytes", type=int, default=2_000_000, help="Read at most this many bytes of any single HTML page.")
    parser.add_argument("--delay", type=float, default=0.5, help="Minimum seconds between requests to the same host.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-sitemaps", type=int, default=4, help="Maximum sitemap files to read per source; use 0 to crawl from seeds only.")
    parser.add_argument("--strict-psa", action="store_true", help="Keep only pages with explicit announcement/advisory signals; default also includes practical extension guidance.")
    parser.add_argument("--use-windows-root-certificates", action="store_true", help="Use Windows' trusted roots for TLS verification on managed Windows networks; verification remains enabled.")
    parser.add_argument("--source", action="append", dest="source_ids", help="Source id to run; repeat to select several.")
    parser.add_argument("--max-workers", type=int, default=0, help="Sources crawled concurrently; 0 = auto (min(number of sources, 8)).")
    parser.add_argument("--user-agent", default="KenyaAgriPSACorpusBot/0.2 (research contact: replace-with-your-email@example.org)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.max_pages_per_source < 1 or args.delay < 0:
        raise SystemExit("--max-pages-per-source must be positive and --delay cannot be negative")
    if args.max_sitemaps < 0:
        raise SystemExit("--max-sitemaps cannot be negative")
    if args.target_records < 1 or args.time_budget_minutes <= 0:
        raise SystemExit("--target-records must be positive and --time-budget-minutes must be > 0")
    sources = read_sources(args.sources)
    if args.source_ids:
        selected = set(args.source_ids)
        unknown = selected - {source["id"] for source in sources}
        if unknown:
            raise SystemExit(f"Unknown source id(s): {', '.join(sorted(unknown))}")
        sources = [source for source in sources if source["id"] in selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    existing_ids, seen_texts = load_existing_ids(args.output)
    num_before = len(existing_ids)
    logging.debug("Destination file %s had %d records before this run.", args.output, num_before)
    write_header = not args.output.exists() or args.output.stat().st_size == 0
    all_reports: dict[str, Any] = {"started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "sources": {}}
    lock = threading.Lock()
    progress = Progress(args.target_records, time.monotonic() + args.time_budget_minutes * 60)

    with args.output.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCHEMA, extrasaction="ignore")
        if write_header:
            writer.writeheader()

        def process_source(source: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            stats = RunStats()
            crawler = LicensedCrawler(args, source, stats, progress)
            pending: list[dict] = []
            try:
                for row in crawler.crawl() or []:
                    dedupe_key = text_hash(row["PSA"])
                    with lock:
                        if row["ID"] in existing_ids or dedupe_key in seen_texts:
                            stats.duplicate_records += 1
                            continue
                        existing_ids.add(row["ID"])
                        seen_texts.add(dedupe_key)
                        stats.records_written += 1
                    pending.append(row)
                    # Batch flush every 20 rows to reduce I/O overhead.
                    if len(pending) >= 20:
                        with lock:
                            writer.writerows(pending)
                            handle.flush()
                        pending.clear()
            except KeyboardInterrupt:
                pass
            except Exception:
                stats.errors += 1
            if pending:
                with lock:
                    writer.writerows(pending)
                    handle.flush()

            domain = host_of(source["seed_urls"][0])
            total_scraped = stats.records_written + stats.duplicate_records
            with lock:
                print(f"Scraping {domain}")
                print(f"Scraped {total_scraped} records ({stats.non_english_skipped} non-English, {stats.relevance_skipped} off-topic skipped)")
                print(f"{stats.duplicate_records} records duplicate")
                print(f"{stats.records_written} records saved")
                print()
            return source["id"], vars(stats)

        max_workers = args.max_workers if args.max_workers > 0 else min(len(sources), 8)
        total_written = 0
        total_duplicates = 0
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_source, src): src for src in sources}
                for future in as_completed(futures):
                    src_id, stats_dict = future.result()
                    all_reports["sources"][src_id] = stats_dict
                    total_written += stats_dict.get("records_written", 0)
                    total_duplicates += stats_dict.get("duplicate_records", 0)
        except KeyboardInterrupt:
            logging.warning("Stopped by user; the CSV already contains completed records.")

    all_reports["finished_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    all_reports["output"] = str(args.output)
    all_reports["schema"] = SCHEMA
    all_reports["stopped_reason"] = (
        "target_records_reached" if progress.count >= args.target_records
        else "time_budget_reached" if time.monotonic() >= progress.deadline_monotonic
        else "all_sources_exhausted"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as handle:
        json.dump(all_reports, handle, indent=2)

    print("=" * 60)
    print(f"Destination file before run : {num_before} records")
    print(f"Records written this run    : {total_written}")
    print(f"Duplicates skipped          : {total_duplicates}")
    print(f"Destination file now        : {num_before + total_written} records")
    print(f"Stopped because             : {all_reports['stopped_reason']}")
    print("=" * 60)
    print(f"Corpus : {args.output}")
    print(f"Report : {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())