import logging
from typing import List, Dict
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def parse_html_elements(html_content: str) -> List[Dict[str, str]]:
    """
    Parse HTML content and extract text from specific elements: title, headings,
    paragraphs, and list items, preserving document order.
    
    Args:
        html_content (str): The raw HTML string to parse.
        
    Returns:
        List[Dict[str, str]]: A list of dictionaries, each containing:
            - 'Element': The tag name (e.g., 'title', 'h1', 'p', 'li')
            - 'Text': The cleaned text content of the element.
    """
    elements = []
    
    try:
        # Using lxml parser for speed and robustness
        soup = BeautifulSoup(html_content, "lxml")
        
        # 1. Extract Page Title
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text().strip()
            if title_text:
                elements.append({
                    "Element": "title",
                    "Text": " ".join(title_text.split())
                })
        
        # 2. Extract Headings, Paragraphs, and List Items in document order
        # We query the body to avoid duplicate headers/footers if possible, 
        # but fallback to searching the whole soup if body is not present.
        container = soup.find("body") or soup
        
        target_tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]
        found_tags = container.find_all(target_tags)
        
        for tag in found_tags:
            text = tag.get_text().strip()
            if text:
                cleaned_text = " ".join(text.split())
                # Only add if we got meaningful text content
                if cleaned_text:
                    elements.append({
                        "Element": tag.name,
                        "Text": cleaned_text
                    })
                    
        logger.info(f"Successfully parsed {len(elements)} textual elements.")
    except Exception as e:
        logger.error(f"Error occurred during HTML parsing: {e}")
        raise e
        
    return elements
