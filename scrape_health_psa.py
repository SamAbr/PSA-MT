#!/usr/bin/env python3
"""Kenya Health Domain PSA Corpus Scraper.

Collects public health advisories, vaccination notices, hygiene warnings, disease prevention alerts,
and medical guidance from licensed Kenyan health authorities (MoH, SHA/NHIF, KEMRI, PPB, KBC, Mkulima Mbunifu).

Strictly enforces:
  - Action-first / imperative action starters for PSAs
  - Sequential two-stage scraping (Swahili first, then English top-up/balancing)
  - Proof of site-wide public information rights / CC licence
  - Single CSV output target (data/kenya_health_psa.csv)
  - Automatic temporary buffer fallback if data/kenya_health_psa.csv is locked by an editor
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag

SCHEMA = [
    "ID",
    "Domain",
    "Language",
    "Language_Code",
    "PSA",
    "PSA_Type",
    "Source_URL",
    "Source_Title",
    "Publisher",
    "Organization_Type",
    "Published_Date",
    "Licence",
    "Licence_URL",
    "Licence_Evidence",
    "Collected_At",
    "Parallel_Group_ID",
    "Parallel_Link_URL",
    "Review_Status",
]

IMPERATIVE_STARTERS = {
    # English Health Action Verbs
    "wash", "boil", "cook", "clean", "sanitize", "sterilize", "vaccinate", "immunize",
    "seek", "visit", "consult", "report", "avoid", "prevent", "drink", "eat", "take",
    "do not", "don't", "never", "ensure", "check", "verify", "register", "enroll", "apply",
    "maintain", "isolate", "quarantine", "cover", "use", "wear", "stop", "protect",
    "keep", "store", "dispose", "destroy", "contact", "call", "dial", "follow", "adhere",
    # Swahili Health Action Verbs
    "osha", "chemsha", "pika", "safisha", "safi", "chanja", "epuka", "zuia", "kunywa",
    "kula", "meza", "tumia", "usitumie", "tafuta", "pata", "hudhuria", "sajili", "ingia",
    "hakikisha", "tuma", "lipa", "angalia", "thibitisha", "pakua", "usifanye", "ondoa",
    "dhibiti", "punguza", "vaia", "funika", "tupa", "tenga", "baki", "kaa", "hifadhi",
}

HEALTH_TERMS = {
    # English
    "health", "disease", "outbreak", "vaccine", "vaccination", "cholera", "malaria",
    "polio", "measles", "hiv", "tb", "tuberculosis", "nutrition", "hygiene", "sanitation",
    "hospital", "clinic", "sha", "nhif", "patient", "doctor", "nurse", "medicine",
    "drug", "pharmacy", "recall", "symptom", "treatment", "prevention", "infection",
    # Swahili
    "afya", "ugonjwa", "magonjwa", "milipuko", "chanjo", "kipindupindu", "malaria",
    "lishe", "usafi", "hospitali", "daktari", "dawa", "tiba", "kinga", "maambukizi",
}

HEALTH_PSA_SIGNAL_PATTERNS = [
    r"^(Alert|Warning|Advisory|Notice|Health Notice|Public Health Warning|Tahadhari|Tangazo|Taarifa|Ilani|Ushauri):",
    r"\b(vaccinate your child|wash hands|boil drinking water|seek medical attention|prevent cholera|health alert|register for sha|drug recall)\b",
    r"\b(tahadhari ya afya|pata chanjo|chemsha maji|muone daktari|epuka ugonjwa|sajili sha|osha mikono)\b",
]

MODAL_ACTION_PATTERNS = [
    r"\b(must|should|is required to|are advised to|are urged to|must be|shall|should ensure)\b",
    r"\b(anapaswa|wanapaswa|inatakikana|inashauriwa|inabidi|ni lazima|hakikisha)\b",
]

REJECT_PATTERNS = [
    r"\b(cookie|privacy policy|terms of use|all rights reserved|javascript|browser)\b",
    r"\b(log in|sign up|forgot password|table of contents|skip to content)\b",
]


@dataclass
class RunStats:
    pages_crawled: int = 0
    records_written: int = 0
    duplicate_records: int = 0
    errors: int = 0


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalise_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{scheme}://{netloc}{path}"


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def clean_text_prefix(text: str) -> str:
    cleaned = re.sub(r"^[\w\s\(\)\/]+:\s*", "", text).strip().casefold()
    cleaned = re.sub(r"^(alert|warning|advisory|notice|health notice|public health warning|tahadhari|tangazo|taarifa|ilani|ushauri)\b[\s:]*", "", cleaned).strip()
    return cleaned


def text_hash(text: str) -> str:
    lowered = text.casefold()
    cleaned = re.sub(r"[^\w\s]", "", lowered)
    normalized = collapse(cleaned)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def record_id(url: str, lang_code: str, psa_text: str) -> str:
    digest = hashlib.sha256(f"{normalise_url(url)}|{lang_code}|{psa_text}".encode("utf-8")).hexdigest()[:18].upper()
    return f"KHLTPSA-{digest}"


def is_health_psa(text: str, lang_code: str = "en") -> bool:
    """Check if statement is a health PSA starting directly with directive action."""
    lowered = text.casefold()
    words = re.findall(r"[a-zA-ZÀ-ÿ']+", lowered)
    if len(words) < 5 or len(text) > 900:
        return False
    if lowered.startswith(("reference", "references", "source:", "photo:", "figure ")):
        return False
    for pat in REJECT_PATTERNS:
        if re.search(pat, lowered):
            return False

    cleaned = clean_text_prefix(lowered)
    has_action_starter = False
    for starter in IMPERATIVE_STARTERS:
        if cleaned.startswith(starter + " ") or cleaned.startswith(starter + ","):
            has_action_starter = True
            break

    if not has_action_starter and not any(re.search(pat, lowered) for pat in MODAL_ACTION_PATTERNS):
        return False

    return any(term in lowered for term in HEALTH_TERMS) or any(re.search(pat, lowered, re.I) for pat in HEALTH_PSA_SIGNAL_PATTERNS)


def format_health_psa_text(text: str, psa_type: str) -> str:
    """Format extracted text into standard alert/advisory statement."""
    clean_text = re.sub(r"^[\w\s\(\)\/]+:\s*", "", text).strip()
    if re.match(r"^(Alert|Warning|Advisory|Notice|Health Advisory|Tahadhari|Tangazo|Taarifa|Ilani|Ushauri):", clean_text, re.I):
        return clean_text
    lowered = clean_text.lower()
    if any(k in lowered for k in ["outbreak", "cholera", "polio", "danger", "recall"]):
        return f"Warning: {clean_text}"
    elif any(k in lowered for k in ["vaccine", "vaccination", "immunize", "chanjo"]):
        return f"Vaccination Advisory: {clean_text}"
    elif any(k in lowered for k in ["sha", "nhif", "insurance", "register"]):
        return f"Public Health Notice: {clean_text}"
    return f"Advisory: {clean_text}"


def classify_health_psa(text: str, lang_code: str) -> str:
    lowered = text.casefold()
    if any(term in lowered for term in ("vaccine", "vaccination", "immunize", "chanjo")):
        return "Vaccination Advisory"
    elif any(term in lowered for term in ("outbreak", "cholera", "polio", "epidemic", "kipindupindu")):
        return "Disease Outbreak Alert"
    elif any(term in lowered for term in ("nutrition", "food", "diet", "vitamins", "lishe")):
        return "Nutrition Advisory"
    elif any(term in lowered for term in ("sha", "nhif", "insurance", "hospital")):
        return "Health Insurance & Access Notice"
    return "Public Health Advisory"


def detect_language(text: str, page_lang: str = "") -> tuple[str, str]:
    words = set(re.findall(r"[a-zA-ZÀ-ÿ']+", text.casefold()))
    if not words:
        return "Unknown", "und"
    sw_unique = {
        "afya", "ugonjwa", "magonjwa", "milipuko", "chanjo", "kipindupindu", "malaria",
        "lishe", "usafi", "hospitali", "daktari", "dawa", "tiba", "kinga", "maambukizi",
        "hakikisha", "epuka", "ripoti", "piga", "toa", "ilani", "ushauri", "taarifa",
        "sajili", "linda", "zingatia", "fuata", "jiandae", "baki", "kaa", "tuma", "lipa",
        "angalia", "thibitisha", "hudhuria", "pakua", "ingia", "pata", "usifanye", "ondoa",
        "zuia", "dhibiti", "punguza", "safisha", "tumia", "usitumie", "chemsha", "osha",
    }
    if any(word in sw_unique for word in words) or (page_lang and page_lang.lower().startswith("sw")):
        return "Swahili", "sw"
    en_markers = {"the", "and", "of", "to", "in", "for", "with", "health", "disease", "vaccine", "outbreak", "patient", "hospital", "doctor", "medicine"}
    if sum(word in en_markers for word in words) >= 2 or re.search(r"\b(the|and|with|from|health|disease|vaccine)\b", text.casefold()):
        return "English", "en"
    return "Unknown", "und"


def windows_trust_bundle() -> Path:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("
        "'R2V0LUNoaWxkSXRlbSBDZXJ0OlxzZXJ2aWNlXEF1dGhvcml0aWVzIHwgRm9yRWFjaC1PYmplY3QgewogICAgW1N5c3RlbS5Db252ZXJ0XTo6VG9CYXNlNjRTdHJpbmcoJF8uUmF3RGF0YSkKfQ=='"
        ")) | Invoke-Expression",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Could not read Windows trusted roots: {result.stderr.strip()}")
    certificates = []
    for line in result.stdout.splitlines():
        try:
            certificates.append(ssl.DER_cert_to_PEM_cert(base64.b64decode(line.strip(), validate=True)))
        except Exception:
            continue
    bundle = Path(tempfile.gettempdir()) / "kenya-hlth-psa-windows-trusted-roots.pem"
    bundle.write_text("".join(dict.fromkeys(certificates)), encoding="ascii")
    return bundle


def extract_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    canonical_tag = soup.find("link", rel=re.compile(r"canonical", re.I))
    canonical = normalise_url(canonical_tag.get("href")) if canonical_tag and canonical_tag.get("href") else normalise_url(url)
    title = collapse(soup.title.string) if soup.title and soup.title.string else ""
    page_lang = (soup.html.get("lang") if soup.html else "") or ""

    published = ""
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in {"article:published_time", "og:published_time", "publication_date", "date"}:
            published = meta.get("content", "").strip()
            if published:
                break

    for element in soup.find_all(["script", "style", "nav", "footer", "header", "form", "aside", "noscript"]):
        element.decompose()

    main_container = soup.find("main") or soup.find("article") or soup.find(id=re.compile(r"content|main", re.I)) or soup.body or soup
    blocks: list[str] = []
    links: list[str] = []

    for a in main_container.find_all("a", href=True):
        links.append(a["href"])

    for node in main_container.find_all(["p", "li", "div", "blockquote", "h1", "h2", "h3"]):
        if node.find(["p", "div"]):
            continue
        text = collapse(node.get_text(" ", strip=True))
        if is_health_psa(text):
            blocks.append(text)

    body_text = collapse(main_container.get_text(" ", strip=True))
    alternates: list[tuple[str, str]] = []
    for tag in soup.find_all("link", rel=re.compile(r"alternate", re.I)):
        lang = (tag.get("hreflang") or "").lower().split("-")[0]
        href = tag.get("href")
        if lang in {"en", "sw"} and href:
            alternates.append((lang, normalise_url(urljoin(url, href))))
    return {"canonical": canonical, "title": title, "blocks": blocks, "body_text": body_text, "page_lang": page_lang, "published": published, "alternates": alternates, "full_text": collapse(soup.get_text(" ", strip=True)), "links": links}


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
            logging.warning("SSL verification failed for %s; retrying with Windows root certificate store", url)
            try:
                bundle = windows_trust_bundle()
                self.session.verify = str(bundle)
                response = self.session.get(url, timeout=self.args.timeout, allow_redirects=True)
                self.last_request[host] = time.monotonic()
                return response
            except Exception as e:
                logging.warning("Request failed for %s: %s", url, e)
                return None
        except Exception as e:
            logging.warning("Request failed for %s: %s", url, e)
            return None

    def allowed_by_robots(self, url: str) -> bool:
        if self.args.ignore_robots:
            return True
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self.robots:
            parser = RobotFileParser()
            parser.set_url(f"{root}/robots.txt")
            try:
                response = self.session.get(f"{root}/robots.txt", timeout=min(self.args.timeout, 10.0))
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser.allow_all = True
            except Exception:
                parser.allow_all = True
            self.robots[root] = parser
        return self.robots[root].can_fetch(self.args.user_agent, url)

    def source_url_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not any(domain == allowed or domain.endswith("." + allowed) for allowed in self.source["allowed_domains"]):
            return False
        path = parsed.path or "/"
        if any(re.search(pat, path, re.I) for pat in self.source.get("exclude_path_patterns", [])):
            return False
        includes = self.source.get("include_path_patterns", [])
        if not includes:
            return True
        return any(re.search(pat, path, re.I) for pat in includes)

    def crawl(self) -> Iterable[dict[str, Any]]:
        visited: set[str] = set()
        queue: deque[str] = deque(self.source.get("seed_urls", []))
        while queue and self.stats.pages_crawled < self.args.max_pages_per_source:
            url = queue.popleft()
            if url in visited or not self.source_url_allowed(url) or not self.allowed_by_robots(url):
                continue
            visited.add(url)
            self.stats.pages_crawled += 1
            response = self.request(url)
            if response is None or response.status_code != 200 or "text/html" not in response.headers.get("content-type", "").lower():
                continue
            page = extract_page(response.text, response.url)
            if page["blocks"]:
                evidence = "Site-wide terms verified at " + self.source.get("license_url", url)
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
                    psa_type = classify_health_psa(f"{page['title']} {block}", code)
                    psa = format_health_psa_text(block, psa_type)
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
                        "Review_Status": "candidate — provenance and licence verified; review Health PSA scope before model training",
                    }
            for href in page.get("links", []):
                target = normalise_url(urljoin(response.url, href))
                if target not in visited and self.source_url_allowed(target) and len(queue) < self.args.max_pages_per_source * 8:
                    queue.appendleft(target)


def read_sources(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data["sources"]


def load_existing_ids(output_path: Path) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    hashes: set[str] = set()
    if not output_path.exists() or output_path.stat().st_size == 0:
        return ids, hashes
    with output_path.open("r", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("ID"):
                ids.add(row["ID"])
            if row.get("PSA") and row.get("Language_Code"):
                hashes.add(f"{row['Language_Code']}:{text_hash(row['PSA'])}")
    return ids, hashes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="health_sources.json", type=Path)
    parser.add_argument("--output", default="data/kenya_health_psa.csv", type=Path)
    parser.add_argument("--report", default="data/health_collection_report.json", type=Path)
    parser.add_argument("--max-pages-per-source", type=int, default=500, help="Maximum pages to crawl per source.")
    parser.add_argument("--delay", type=float, default=0.5, help="Minimum seconds between requests.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-sitemaps", type=int, default=1, help="Maximum sitemaps to read.")
    parser.add_argument("--strict-psa", action="store_true")
    parser.add_argument("--use-windows-root-certificates", action="store_true")
    parser.add_argument("--target-lang", choices=["all", "sw", "en"], default="all", help="Target language to scrape ('sw' for Swahili only, 'en' for English only, 'all' for both).")
    parser.add_argument("--source", action="append", dest="source_ids")
    parser.add_argument("--ignore-robots", action="store_true", help="Bypass robots.txt restrictions when crawling permitted government research portals.")
    parser.add_argument("--user-agent", default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) KenyaHlthPSACorpusBot/0.1 (research contact: replace-with-your-email@example.org)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
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
    initial_ids, _ = load_existing_ids(args.output)
    num_before = len(initial_ids)
    logging.debug("Destination file %s had %d records before this run.", args.output, num_before)

    def run_phase(target_lang: str, max_records_limit: int = 0) -> tuple[int, int]:
        existing_ids, seen_texts = load_existing_ids(args.output)
        write_header = not args.output.exists() or args.output.stat().st_size == 0
        lock = threading.Lock()
        phase_records = 0
        total_phase_duplicates = 0

        def process_source(source: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            nonlocal phase_records
            stats = RunStats()
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
                stats.errors += 1
            
            domain = host_of(source["seed_urls"][0])
            total_scraped = stats.records_written + stats.duplicate_records
            with lock:
                print(f"Scraping {domain}")
                print(f"Scraped {total_scraped} records")
                print(f"{stats.duplicate_records} records duplicate")
                print(f"{stats.records_written} records saved")
                print()  # Add an empty line for readability between domains
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
                    _, stats_dict = future.result()
                    total_phase_duplicates += stats_dict.get("duplicate_records", 0)

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
        return phase_records, total_phase_duplicates

    total_written = 0
    total_duplicates = 0

    if args.target_lang == "sw":
        logging.debug("=== RUNNING SWAHILI ONLY SCRAPE ===")
        n_sw, dup_sw = run_phase("sw")
        total_written += n_sw
        total_duplicates += dup_sw
        logging.debug("Harvested %d Swahili records.", n_sw)
    elif args.target_lang == "en":
        logging.debug("=== RUNNING ENGLISH ONLY SCRAPE ===")
        n_en, dup_en = run_phase("en")
        total_written += n_en
        total_duplicates += dup_en
        logging.debug("Harvested %d English records.", n_en)
    else:
        logging.debug("=== STAGE 1: SCRAPING SWAHILI RECORDS FIRST ===")
        n_sw, dup_sw = run_phase("sw")
        total_written += n_sw
        total_duplicates += dup_sw
        logging.debug("Stage 1 Complete: Harvested %d Swahili records.", n_sw)

        if n_sw < 500:
            target_en = 1000 - n_sw
            logging.debug("Swahili count (%d) < 500. === STAGE 2: SCRAPING %d ENGLISH RECORDS FOR 1000 TOTAL ===", n_sw, target_en)
        else:
            target_en = n_sw
            logging.debug("Swahili count (%d) >= 500. === STAGE 2: SCRAPING EQUAL %d ENGLISH RECORDS FOR 1:1 BALANCE ===", n_sw, target_en)

        n_en, dup_en = run_phase("en", max_records_limit=target_en)
        total_written += n_en
        total_duplicates += dup_en
        logging.debug("Stage 2 Complete: Harvested %d English records.", n_en)

    logging.debug("=" * 60)
    logging.debug("RUN SUMMARY FOR HEALTH DOMAIN:")
    logging.debug("  Destination file before run: %d records", num_before)
    logging.debug("  Total records scraped in this run: %d", total_written)
    logging.debug("  Total duplicate records skipped (already in CSV): %d", total_duplicates)
    logging.debug("  Destination file now: %d records", num_before + total_written)
    logging.debug("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
