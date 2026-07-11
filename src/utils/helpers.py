"""
Shared helper utilities for logging, file loading, and simple string mappings.
"""

def setup_logger(name: str):
    """Simple logger config builder."""
    import logging
    logger = logging.getLogger(name)
    return logger
