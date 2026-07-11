import logging
import os
import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Standard browser headers to prevent basic scraping blocks
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def fetch_html(url: str, use_selenium: bool = False, timeout: int = 15) -> str:
    """
    Fetch the raw HTML content of a URL.
    
    Args:
        url (str): The target URL to fetch.
        use_selenium (bool): Whether to use Selenium fallback for dynamic JS rendering.
        timeout (int): Timeout in seconds for requests.
        
    Returns:
        str: Raw HTML content.
        
    Raises:
        Exception: If the page cannot be fetched.
    """
    if url.startswith("file://"):
        logger.info(f"Fetching local file: {url}")
        try:
            from urllib.request import url2pathname
            # Remove 'file://' and parse path
            # url[7:] strips 'file://'
            path = url2pathname(url[7:])
            # Clean up leading slash on Windows if it is absolute
            if os.name == 'nt':
                # Handle cases like /C:/path or \C:\path
                if path.startswith('\\') or path.startswith('/'):
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
            chrome_options.add_argument(f"user-agent={DEFAULT_HEADERS['User-Agent']}")
            
            driver = webdriver.Chrome(options=chrome_options)
            try:
                driver.get(url)
                # Allow basic JS execution time
                driver.implicitly_wait(timeout)
                html = driver.page_source
                return html
            finally:
                driver.quit()
        except ImportError:
            logger.error("Selenium is not installed. Cannot use Selenium fallback. Run 'pip install selenium'.")
            raise ImportError("Selenium is required when use_selenium=True, but is not installed.")
        except Exception as e:
            logger.error(f"Error fetching with Selenium: {e}")
            raise e
    else:
        logger.info(f"Fetching (requests): {url}")
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error fetching {url}: {e}")
            raise e
