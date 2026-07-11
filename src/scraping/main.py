import argparse
import os
import sys
import logging

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("scraper_main")

# Add the project root to sys.path so we can run this module directly
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.scraping.fetch import fetch_html
from src.scraping.parser import parse_html_elements
from src.scraping.exporter import export_elements_to_csv

def run_pipeline(url: str, output_path: str, use_selenium: bool = False):
    """
    Run the scraper pipeline.
    1. Fetch raw HTML.
    2. Parse title, headings, paragraphs, and list items.
    3. Save results to a CSV file.
    """
    logger.info("=" * 60)
    logger.info("Starting PSA Scraper Pipeline (Proof of Concept)")
    logger.info(f"Target URL:  {url}")
    logger.info(f"Output File: {output_path}")
    logger.info(f"Selenium:    {use_selenium}")
    logger.info("=" * 60)

    try:
        # Step 1: Fetch HTML
        html_content = fetch_html(url, use_selenium=use_selenium)
        logger.info(f"Successfully retrieved {len(html_content)} bytes of HTML content.")
        
        # Step 2: Parse HTML
        parsed_elements = parse_html_elements(html_content)
        
        # Step 3: Export to CSV
        export_elements_to_csv(parsed_elements, url, output_path)
        logger.info("Pipeline executed successfully!")
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run proof-of-concept scraper for Kenya Ministry of Agriculture & Livestock Development."
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://www.kilimo.go.ke",
        help="Target URL to scrape (default: https://www.kilimo.go.ke)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(project_root, "data", "raw", "scraped", "kilimo_raw.csv"),
        help="Path to save the output CSV file (default: data/raw/scraped/kilimo_raw.csv)"
    )
    parser.add_argument(
        "--selenium",
        action="store_true",
        help="Use Selenium webdriver fallback to render JavaScript dynamically."
    )
    
    args = parser.parse_args()
    
    run_pipeline(
        url=args.url,
        output_path=args.output,
        use_selenium=args.selenium
    )
