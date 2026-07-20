#!/usr/bin/env python3
"""Build an auditable English/Swahili Kenyan Education PSA corpus from licensed government and official pages.

The program collects sources configured with explicit re-use terms, obeys robots.txt (with --ignore-robots flag option),
throttles requests, handles SSL certificate errors, and writes formatted, action-first PSA records.
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
from collections import Counter, deque
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

SCHEMA = [
    "ID", "Domain", "Language", "Language_Code", "PSA", "PSA_Type",
    "Source_URL", "Source_Title", "Publisher", "Organization_Type",
    "Published_Date", "Licence", "Licence_URL", "Licence_Evidence",
    "Collected_At", "Parallel_Group_ID", "Parallel_Link_URL", "Review_Status",
]

EDU_TERMS = {
    "en": (
        "education", "school", "student", "pupil", "teacher", "tutor", "curriculum",
        "examination", "exam", "knec", "kcse", "kcpe", "kpsea", "cbc", "bursary",
        "scholarship", "helb", "kuccps", "placement", "university", "college",
        "tuition", "admission", "term", "tveta", "tsc", "polytechnic", "grade",
        "class", "learner", "institution", "course", "diploma", "degree",
    ),
    "sw": (
        "elimu", "shule", "mwanafunzi", "wanafunzi", "mwalimu", "walimu", "mtihani",
        "mitihani", "mcurrikulamu", "masomo", "fursa", "ufadhili", "mkopo", "mikopo",
        "chuo kikuu", "vyuo vikuu", "muhula", "usajili", "shahada", "stashahada",
        "masomo", "masomo ya mwanzo", "masomo ya msingi",
    ),
}

PSA_SIGNALS = {
    "en": (
        "advisory", "alert", "notice", "announcement", "update", "warning", "guidance",
        "recommend", "students should", "must", "how to", "tips", "deadline", "application",
        "registration", "apply", "placement", "bursary", "call for", "public participation",
    ),
    "sw": (
        "tahadhari", "tangazo", "taarifa", "ushauri", "waelekezwe", "wanapaswa", "lazima",
        "jinsi ya", "mapendekezo", "tarehe ya mwisho", "maombi", "usajili", "tuma", "jiandae",
    ),
}

KENYA_TERMS = (
    "kenya", "kenyan", "nairobi", "mombasa", "kisumu", "nakuru", "eldoret",
    "kiambu", "makueni", "kajiado", "turkana", "kiswahili", "knec", "kuccps", "helb", "tsc",
)

SKIP_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".mp3", ".mp4",
    ".avi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
)

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi"}

IMPERATIVE_STARTERS = [
    # English Imperative Verbs
    "activate", "apply", "attend", "avoid", "check", "clean", "collect", "confirm",
    "cooperate", "create", "destroy", "download", "ensure", "heed", "inspect",
    "log in", "maintain", "manage", "minimize", "monitor", "note", "observe", "obtain",
    "participate", "pay", "prepare", "prevent", "protect", "read", "register",
    "renew", "report", "review", "sanitize", "say no", "select", "stay clear", "store",
    "submit", "track", "transfer", "update", "upload", "use", "verify", "visit",
    # Educational Directive Expressions
    "all students", "all candidates", "all teachers", "all applicants", "all parents",
    "all schools", "all universities", "all colleges", "the ministry of education",
    "the council", "the commission", "notice is hereby given", "the general public",
    "admissions", "applications are invited", "the deadline for", "the results for",
    "the placement of", "the registration of", "to apply for", "for more information",
    "please note that", "take note that", "it is notified that", "we wish to notify",
    "this is to inform", "members of the public", "first year students", "continuing students",
    # English Modifiers & Starters
    "always", "never", "do not", "immediately", "to apply", "to register", "to prevent",
    "students must", "students should", "parents should", "teachers should",
    "candidates must", "candidates should", "applicants must", "applicants should",
    # Swahili Imperative Verbs & Starters
    "ondoa", "zuia", "dhibiti", "tumia", "epuka", "hakikisha", "punguza", "toa taarifa",
    "jiandae", "safisha", "sajili", "tuma", "lipa", "angalia", "thibitisha", "hudhuria",
    "pakua", "ingia", "pata", "usitumie", "usifanye", "wanafunzi wanapaswa", "walimu wanapaswa",
    "wanafunzi wote", "walimu wote", "shule zote", "wazazi wote", "maombi ya", "usajili wa",
    "wito wa", "taarifa kwa umma", "tangazo kwa", "tarehe ya mwisho", "mtihani wa",
]

MODAL_ACTION_PATTERNS = [
    r"\b(students?|candidates?|parents?|teachers?|applicants?)\s+(should|must|are advised|need to)\b",
    r"\b(should|must) (be|have|apply|register|submit|verify|check|pay|confirm)\b",
    r"\b(is|are) recommended\b",
]

REJECT_PATTERNS = [
    r"\b(is a channel of|provides scientific and practical)\b",
    r"\b(magazine with practical information|publications provide a range of)\b",
]


@dataclass
class RunStats:
    pages_seen: int = 0
    pages_fetched: int = 0
    records_written: int = 0
    duplicate_records: int = 0
    robots_skipped: int = 0
    licence_skipped: int = 0
    relevance_skipped: int = 0
    errors: int = 0


def collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def normalise_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query, doseq=True), ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().split(":")[0]


def text_hash(text: str) -> str:
    return hashlib.sha256(collapse(text).casefold().encode("utf-8")).hexdigest()


def record_id(url: str, language: str, psa: str) -> str:
    digest = hashlib.sha256(f"{normalise_url(url)}\0{language}\0{collapse(psa)}".encode("utf-8")).hexdigest()[:18].upper()
    return f"KEPSA-{digest}"


def windows_trust_bundle() -> Path:
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
    bundle = Path(tempfile.gettempdir()) / "kenya-edu-psa-windows-trusted-roots.pem"
    bundle.write_text("".join(dict.fromkeys(certificates)), encoding="ascii")
    return bundle


def detect_language(text: str, page_lang: str = "") -> tuple[str, str]:
    words = set(re.findall(r"[a-zA-ZÀ-ÿ']+", text.casefold()))
    if not words:
        return "Unknown", "und"
    sw_unique = {
        "hakikisha", "mifugo", "chanjo", "shamba", "chakula", "udongo", "mbegu", "mimea",
        "safisha", "tumia", "usitumie", "ondoa", "zuia", "dhibiti", "epuka", "punguza",
        "jiandae", "sajili", "tuma", "lipa", "angalia", "thibitisha", "hudhuria", "pakua",
        "ingia", "pata", "usifanye", "wanafunzi", "walimu", "mtihani", "mitihani", "masomo",
        "ufadhili", "mkopo", "mikopo", "usajili", "katika", "lakini", "watu", "mkulima",
        "wakulima", "kilimo", "mazao", "mifugo", "ngombe", "kuku", "mbuzi", "magonjwa",
        "wadudu", "ukame", "mvua", "umwagiliaji", "elimu", "shule", "mwalimu", "mwanafunzi",
    }
    if any(word in sw_unique for word in words) or (page_lang and page_lang.lower().startswith("sw")):
        return "Swahili", "sw"
    en_markers = {"the", "and", "of", "to", "in", "for", "with", "farmers", "agriculture", "crop", "livestock", "weather", "market", "students", "education", "school", "exam", "teachers"}
    if sum(word in en_markers for word in words) >= 2 or re.search(r"\b(the|and|with|from|farmers?|students?)\b", text.casefold()):
        return "English", "en"
    return "Unknown", "und"


def has_term(text: str, terms: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in terms)


def classify_edu_psa(text: str, lang_code: str) -> str:
    lowered = text.casefold()
    if any(term in lowered for term in ("knec", "kcse", "kcpe", "kpsea", "exam", "mitihani", "mtihani", "kuccps", "placement", "index number")):
        return "Exam or Placement Notice"
    if any(term in lowered for term in ("helb", "bursary", "scholarship", "loan", "funding", "disbursement", "mkopo", "mikopo", "ufadhili")):
        return "Funding or Bursary Alert"
    if any(term in lowered for term in ("term", "calendar", "opening", "closing", "holiday", "muhula", "likizo")):
        return "School Calendar or Term Notice"
    if any(term in lowered for term in ("tsc", "teacher", "recruitment", "registration", "walimu", "mwalimu")):
        return "Teacher or Institutional Guidance"
    return "Educational Advisory"


def clean_text_prefix(text: str) -> str:
    return re.sub(r"^[\w\s\(\)\/]+:\s*", "", text).strip()


def is_usable_text_block(block: str) -> bool:
    lowered = block.casefold()
    word_count = len(re.findall(r"\b[\w'-]+\b", block))
    if word_count < 5 or len(block) > 900:
        return False
    if re.search(r"(?:https?://|www\.)", lowered) or "isbn" in lowered or "©" in block:
        return False
    if lowered.startswith(("reference", "references", "bibliography", "source:", "photo:", "figure ")):
        return False
    for pat in REJECT_PATTERNS:
        if re.search(pat, lowered):
            return False

    cleaned = clean_text_prefix(lowered)
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", cleaned) if s.strip()]
    for sentence in sentences:
        s_clean = clean_text_prefix(sentence)
        for starter in IMPERATIVE_STARTERS:
            if s_clean.startswith(starter + " ") or s_clean.startswith(starter + ",") or s_clean == starter:
                return True
        if any(re.search(pat, sentence) for pat in MODAL_ACTION_PATTERNS):
            return True

    return False


def format_psa_text(text: str, psa_type: str) -> str:
    clean_text = clean_text_prefix(text)
    if re.match(r"^(Alert|Warning|Advisory|Notice|Emergency Update|Security Notice|Tahadhari|Tangazo|Taarifa|Ushauri):", clean_text, re.I):
        return clean_text
    lowered = clean_text.lower()
    if any(kw in lowered for kw in ["deadline", "closing", "urgent", "warning", "never", "do not"]):
        prefix = "Warning"
    elif any(kw in lowered for kw in ["exam", "knec", "kuccps", "placement", "registration", "helb", "bursary"]):
        prefix = "Notice"
    elif any(kw in lowered for kw in ["calendar", "term", "holiday", "opening"]):
        prefix = "School Notice"
    else:
        prefix = "Educational Advisory"
    return f"{prefix}: {clean_text}"


def is_relevant(title: str, page_text: str, source: dict[str, Any], strict_psa: bool) -> bool:
    combined = f"{title} {page_text}".casefold()
    language = "sw" if has_term(combined, EDU_TERMS["sw"]) and not has_term(combined, EDU_TERMS["en"]) else "en"
    if not has_term(combined, EDU_TERMS[language]):
        if not any(has_term(combined, terms) for terms in EDU_TERMS.values()):
            return False
    if source.get("kenya_context_required") and not has_term(combined, KENYA_TERMS):
        return False
    if strict_psa:
        return any(has_term(combined, terms) for terms in PSA_SIGNALS.values())
    return True


def extract_page(html: str | bytes, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = [tag.get("href") for tag in soup.find_all("a", href=True) if tag.get("href")]
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
        main = soup.find(class_=re.compile(r"(content|entry|article|post|main)", re.I)) or soup.body or soup
    blocks: list[str] = []
    for tag in main.find_all(["p", "li", "h2", "h3", "h4"]):
        block = collapse(tag.get_text(" ", strip=True))
        if 70 <= len(block) <= 1800 and not re.fullmatch(r"[\W\d_]+", block) and is_usable_text_block(block):
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
    alternates: list[tuple[str, str]] = []
    for tag in soup.find_all("link", rel=lambda value: value and "alternate" in value):
        lang = (tag.get("hreflang") or "").lower().split("-")[0]
        href = tag.get("href")
        if lang in {"en", "sw"} and href:
            alternates.append((lang, normalise_url(urljoin(url, href))))
    return {"canonical": canonical, "title": title, "blocks": blocks, "body_text": body_text, "page_lang": page_lang, "published": published, "alternates": alternates, "full_text": collapse(soup.get_text(" ", strip=True)), "links": links}


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
    def __init__(self, args: argparse.Namespace, source: dict[str, Any], stats: RunStats):
        self.args = args
        self.source = source
        self.stats = stats
        self.session = requests.Session()
        self.session.max_redirects = 5
        self.session.headers.update({
            "User-Agent": args.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,sw;q=0.8",
        })
        if args.use_windows_root_certificates:
            bundle = windows_trust_bundle()
            self.session.verify = str(bundle)
            logging.info("Using Windows trusted-root bundle for TLS verification")
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def request(self, url: str) -> requests.Response | None:
        host = host_of(url)
        elapsed = time.monotonic() - self.last_request.get(host, 0)
        if elapsed < self.args.delay:
            time.sleep(self.args.delay - elapsed)
        try:
            response = self.session.get(url, timeout=self.args.timeout, allow_redirects=True)
            self.last_request[host] = time.monotonic()
            return response
        except requests.exceptions.SSLError:
            try:
                response = self.session.get(url, timeout=self.args.timeout, allow_redirects=True, verify=False)
                self.last_request[host] = time.monotonic()
                return response
            except requests.RequestException as exc:
                logging.warning("Request failed after SSL fallback: %s (%s)", url, exc)
                self.stats.errors += 1
                return None
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
        if getattr(self.args, "ignore_robots", False):
            return True
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
            candidate = normalise_url(queue.popleft())
            if candidate in visited or not self.source_url_allowed(candidate):
                continue
            visited.add(candidate)
            self.stats.pages_seen += 1
            if not self.allowed_by_robots(candidate):
                continue
            response = self.request(candidate)
            if response is None or response.status_code != 200:
                continue
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                continue
            self.stats.pages_fetched += 1
            page = extract_page(response.content, response.url)
            if not is_relevant(page["title"], page["body_text"], self.source, self.args.strict_psa):
                self.stats.relevance_skipped += 1
            else:
                evidence = licence_evidence(page["full_text"], self.source)
                if self.source["license_mode"] == "per_page" and not evidence:
                    self.stats.licence_skipped += 1
                else:
                    alt_url = ""
                    parallel_id = ""
                    for alt_lang, alt in page["alternates"]:
                        if alt_lang in {"en", "sw"} and alt != page["canonical"] and self.source_url_allowed(alt):
                            alt_url = alt
                            parallel_id = "PAIR-" + hashlib.sha256("\0".join(sorted([page["canonical"], alt])).encode("utf-8")).hexdigest()[:16].upper()
                            if alt not in visited:
                                queue.append(alt)
                            break
                    for block in page["blocks"]:
                        src_lang = self.source.get("language") or ("sw" if "mkulimambunifu.org" in page["canonical"] or "kbc.co.ke" in page["canonical"] else page["page_lang"])
                        language, code = detect_language(block, src_lang)
                        if code not in {"en", "sw"}:
                            continue
                        psa_type = classify_edu_psa(f"{page['title']} {block}", code)
                        psa = format_psa_text(block, psa_type)
                        yield {
                            "ID": record_id(page["canonical"], code, psa),
                            "Domain": host_of(page["canonical"]),
                            "Language": language,
                            "Language_Code": code,
                            "PSA": psa,
                            "PSA_Type": psa_type,
                            "Source_URL": page["canonical"],
                            "Source_Title": page["title"],
                            "Publisher": self.source["publisher"],
                            "Organization_Type": self.source["organization_type"],
                            "Published_Date": page["published"],
                            "Licence": self.source["license"],
                            "Licence_URL": self.source.get("license_url", "") or page["canonical"],
                            "Licence_Evidence": evidence,
                            "Collected_At": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                            "Parallel_Group_ID": parallel_id,
                            "Parallel_Link_URL": alt_url,
                            "Review_Status": "candidate — provenance and licence verified; review Education PSA scope before model training",
                        }
            for href in page.get("links", []):
                target = normalise_url(urljoin(response.url, href))
                if target not in visited and self.source_url_allowed(target) and len(queue) < self.args.max_pages_per_source * 8:
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
            if row.get("PSA") and row.get("Language_Code"):
                hashes.add(f"{row['Language_Code']}:{text_hash(row['PSA'])}")
    return ids, hashes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="education_sources.json", type=Path)
    parser.add_argument("--output", default="data/kenya_edu_psa.csv", type=Path)
    parser.add_argument("--report", default="data/education_collection_report.json", type=Path)
    parser.add_argument("--max-pages-per-source", type=int, default=500, help="Maximum pages to crawl per source.")
    parser.add_argument("--delay", type=float, default=0.5, help="Minimum seconds between requests.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-sitemaps", type=int, default=1, help="Maximum sitemaps to read.")
    parser.add_argument("--strict-psa", action="store_true")
    parser.add_argument("--use-windows-root-certificates", action="store_true")
    parser.add_argument("--target-lang", choices=["all", "sw", "en"], default="all", help="Target language to scrape ('sw' for Swahili only, 'en' for English only, 'all' for both).")
    parser.add_argument("--source", action="append", dest="source_ids")
    parser.add_argument("--ignore-robots", action="store_true", help="Bypass robots.txt restrictions when crawling permitted government research portals.")
    parser.add_argument("--user-agent", default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) KenyaEduPSACorpusBot/0.1 (research contact: replace-with-your-email@example.org)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.max_pages_per_source < 1 or args.delay < 0:
        raise SystemExit("--max-pages-per-source must be positive and --delay cannot be negative")
    if args.max_sitemaps < 0:
        raise SystemExit("--max-sitemaps cannot be negative")
    sources = read_sources(args.sources)
    if args.source_ids:
        selected = set(args.source_ids)
        unknown = selected - {source["id"] for source in sources}
        if unknown:
            raise SystemExit(f"Unknown source id(s): {', '.join(sorted(unknown))}")
        sources = [source for source in sources if source["id"] in selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def run_phase(target_lang: str, max_records_limit: int = 0) -> int:
        existing_ids, seen_texts = load_existing_ids(args.output)
        write_header = not args.output.exists() or args.output.stat().st_size == 0
        lock = threading.Lock()
        phase_records = 0

        def process_source(source: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            nonlocal phase_records
            stats = RunStats()
            logging.info("Starting [%s] %s", target_lang.upper(), source["id"])
            crawler = LicensedCrawler(args, source, stats)
            try:
                for row in crawler.crawl() or []:
                    if row["Language_Code"] != target_lang:
                        continue
                    dedupe_key = f"{row['Language_Code']}:{text_hash(row['PSA'])}"
                    with lock:
                        if max_records_limit > 0 and phase_records >= max_records_limit:
                            break
                        if row["ID"] in existing_ids or dedupe_key in seen_texts:
                            stats.duplicate_records += 1
                            continue
                        writer.writerow(row)
                        handle.flush()
                        existing_ids.add(row["ID"])
                        seen_texts.add(dedupe_key)
                        stats.records_written += 1
                        phase_records += 1
            except Exception:
                logging.exception("Unexpected failure in %s", source["id"])
                stats.errors += 1
            logging.info("Finished [%s] %s: %s records", target_lang.upper(), source["id"], stats.records_written)
            return source["id"], vars(stats)

        target_path = args.output
        try:
            handle = target_path.open("a", encoding="utf-8", newline="")
        except PermissionError:
            target_path = args.output.parent / f".tmp_{args.output.name}"
            handle = target_path.open("a", encoding="utf-8", newline="")
            logging.info("Output file %s is locked. Using temporary buffer %s", args.output, target_path)

        with handle:
            writer = csv.DictWriter(handle, fieldnames=SCHEMA, extrasaction="ignore")
            if write_header and target_path == args.output:
                writer.writeheader()
            elif target_path != args.output and (not target_path.exists() or target_path.stat().st_size == 0):
                writer.writeheader()

            max_workers = min(len(sources), 4) if len(sources) > 1 else 1
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_source = {executor.submit(process_source, source): source for source in sources}
                for future in as_completed(future_to_source):
                    future.result()

        if target_path != args.output and target_path.exists():
            try:
                with target_path.open("r", encoding="utf-8") as tmp_in:
                    lines = tmp_in.readlines()
                if args.output.exists() and args.output.stat().st_size > 0:
                    lines = [line for line in lines if not line.startswith("ID,Domain,Language")]
                with args.output.open("a", encoding="utf-8") as main_out:
                    main_out.writelines(lines)
                target_path.unlink()
                logging.info("Merged temporary records into %s", args.output)
            except Exception:
                pass
        return phase_records

    if args.target_lang == "sw":
        logging.info("=== RUNNING SWAHILI ONLY SCRAPE ===")
        n_sw = run_phase("sw")
        logging.info("Harvested %d Swahili records.", n_sw)
    elif args.target_lang == "en":
        logging.info("=== RUNNING ENGLISH ONLY SCRAPE ===")
        n_en = run_phase("en")
        logging.info("Harvested %d English records.", n_en)
    else:
        logging.info("=== STAGE 1: SCRAPING SWAHILI RECORDS FIRST ===")
        n_sw = run_phase("sw")
        logging.info("Stage 1 Complete: Harvested %d Swahili records.", n_sw)

        if n_sw < 500:
            target_en = 1000 - n_sw
            logging.info("Swahili count (%d) < 500. === STAGE 2: SCRAPING %d ENGLISH RECORDS FOR 1000 TOTAL ===", n_sw, target_en)
        else:
            target_en = n_sw
            logging.info("Swahili count (%d) >= 500. === STAGE 2: SCRAPING EQUAL %d ENGLISH RECORDS FOR 1:1 BALANCE ===", n_sw, target_en)

        n_en = run_phase("en", max_records_limit=target_en)
        logging.info("Stage 2 Complete: Harvested %d English records.", n_en)
        logging.info("Corpus Finalized: %d Swahili + %d English = %d Total PSAs in %s", n_sw, n_en, n_sw + n_en, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
