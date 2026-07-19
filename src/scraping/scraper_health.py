import argparse
import os
import sys
import logging
from urllib.request import url2pathname
import requests
from bs4 import BeautifulSoup
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scraper_health")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

def fetch_html(url: str, use_selenium: bool = False, timeout: int = 15) -> str:
    """Fetch the raw HTML content."""
    if url.startswith("file://"):
        logger.info(f"Fetching local file: {url}")
        try:
            path = url2pathname(url[7:])
            if os.name == 'nt' and (path.startswith('\\') or path.startswith('/')):
                if len(path) > 2 and path[2] == ':':
                    path = path[1:]
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading local file {url}: {e}")
            raise e

    if use_selenium:
        logger.info(f"Using Selenium fallback to fetch: {url}")
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--disable-gpu")
            driver = webdriver.Chrome(options=chrome_options)
            try:
                driver.get(url)
                driver.implicitly_wait(timeout)
                return driver.page_source
            finally:
                driver.quit()
        except ImportError:
            logger.error("Selenium is not installed.")
            raise ImportError("Selenium is required when use_selenium=True.")
        except Exception as e:
            logger.error(f"Error fetching with Selenium: {e}")
            raise e

    logger.info(f"Fetching (requests): {url}")
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.exceptions.SSLError:
        logger.warning(f"SSL verification failed for {url}. Retrying with verify=False...")
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, verify=False)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error after SSL bypass: {e}")
            raise e
    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP request error: {e}")
        raise e

def parse_html_elements(html_content: str) -> list:
    """Parse HTML and extract title, headings, paragraphs, and list items."""
    elements = []
    try:
        soup = BeautifulSoup(html_content, "lxml")
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text().strip()
            if title_text:
                elements.append({"Element": "title", "Text": " ".join(title_text.split())})
        
        container = soup.find("body") or soup
        target_tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]
        found_tags = container.find_all(target_tags)
        
        for tag in found_tags:
            text = tag.get_text().strip()
            if text:
                cleaned_text = " ".join(text.split())
                if cleaned_text:
                    elements.append({"Element": tag.name, "Text": cleaned_text})
        logger.info(f"Parsed {len(elements)} textual elements.")
    except Exception as e:
        logger.error(f"Error occurred during HTML parsing: {e}")
        raise e
    return elements

def export_to_csv(elements: list, url: str, output_path: str) -> None:
    """Saves parsed elements to CSV format."""
    if not elements:
        logger.warning(f"No elements to export.")
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        export_data = [{"URL": url, "Element": elem["Element"], "Text": elem["Text"]} for elem in elements]
        df = pd.DataFrame(export_data)
        df.to_csv(output_path, index=False, encoding="utf-8")
        logger.info(f"Successfully exported {len(df)} rows to {output_path}")
    except Exception as e:
        logger.error(f"Failed to export data: {e}")
        raise e

def main():
    parser = argparse.ArgumentParser(description="PSA Health Scraper")
    parser.add_argument("--url", type=str, default="https://www.health.go.ke", help="Target URL to scrape")
    parser.add_argument("--output", type=str, default=None, help="Custom output CSV path")
    parser.add_argument("--selenium", action="store_true", help="Use Selenium fallback")
    args = parser.parse_args()
    
    if args.output is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))
        output_path = os.path.join(project_root, "data", "raw", "scraped", "health.csv")
    else:
        output_path = args.output
        
    logger.info("=" * 60)
    logger.info("Starting Health Scraper")
    logger.info(f"Target URL:  {args.url}")
    logger.info(f"Output File: {output_path}")
    logger.info("=" * 60)
    
    try:
        html = fetch_html(args.url, use_selenium=args.selenium)
        elements = parse_html_elements(html)
        export_to_csv(elements, args.url, output_path)
        logger.info("Health Scraper execution completed successfully.")
    except Exception as e:
        logger.error(f"Scraper execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
