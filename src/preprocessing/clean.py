"""
Text cleaning functions for raw scraped English and Kiswahili texts.
Removes HTML entities, boilerplate, and normalizes whitespaces.
"""

def clean_text(text: str) -> str:
    """Normalize and clean a single string element."""
    if not isinstance(text, str):
        return ""
    # TODO: Add specific regex cleaning
    return text.strip()
