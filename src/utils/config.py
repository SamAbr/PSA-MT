"""
Configuration parameters and constants for the PSA-MT pipeline.
"""
import os

# Base paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")

# Translation settings
DEFAULT_MODEL_CHECKPOINT = "facebook/nllb-200-distilled-600M"
