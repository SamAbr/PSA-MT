import os
import logging
from typing import List, Dict
import pandas as pd

logger = logging.getLogger(__name__)

def export_elements_to_csv(elements: List[Dict[str, str]], url: str, output_path: str) -> None:
    """
    Export extracted elements to a CSV file.
    
    Args:
        elements (List[Dict[str, str]]): List of parsed element dicts with keys 'Element' and 'Text'.
        url (str): The source URL from which the elements were scraped.
        output_path (str): The file path where the CSV should be saved.
    """
    if not elements:
        logger.warning(f"No elements to export for URL: {url}")
        return
        
    try:
        # Ensure the destination directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
        # Prepare data with URL
        export_data = []
        for elem in elements:
            export_data.append({
                "URL": url,
                "Element": elem["Element"],
                "Text": elem["Text"]
            })
            
        # Convert to DataFrame and save
        df = pd.DataFrame(export_data)
        
        # If the file already exists, we can append or overwrite.
        # For a clean start, we'll overwrite by default, but log it.
        if os.path.exists(output_path):
            logger.info(f"File {output_path} already exists. Overwriting with new data.")
            
        df.to_csv(output_path, index=False, encoding="utf-8")
        logger.info(f"Successfully exported {len(df)} rows to {output_path}")
        
    except Exception as e:
        logger.error(f"Failed to export data to CSV: {e}")
        raise e
