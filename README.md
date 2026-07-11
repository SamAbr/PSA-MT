# PSA-MT: Public Service Announcement Machine Translation

This project focuses on crawling, preprocessing, and aligning public service announcements (PSAs) from official sources (such as government websites) to create a parallel corpus for English-Kiswahili Machine Translation.

## Directory Structure

```
PSA-MT/
├── app/                  # Web interface / applications
├── data/                 # Raw, interim, processed, and final datasets
│   ├── raw/
│   │   ├── lecturer/
│   │   ├── scraped/     # Where the scraper stores extracted HTML text
│   │   └── public_corpus/
│   ├── interim/
│   ├── processed/
│   └── final/
├── models/               # Saved model weights and configurations
├── notebooks/            # Jupyter notebooks for experimentation
├── reports/              # Figures, summaries, and write-ups
├── src/                  # Source code for the pipeline
│   ├── scraping/        # Web crawlers and HTML parsers
│   ├── preprocessing/   # Text cleanup and PSA filtering
│   ├── training/        # MT model training scripts
│   ├── evaluation/      # BLEU / COMET validation metrics
│   ├── inference/       # Prediction scripts
│   └── utils/           # Shared helper functions
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation
```

## Getting Started

### Prerequisites

Create a Python virtual environment and install the required packages:

```bash
# Navigate to the project root
cd C:\Users\Admin\.gemini\antigravity-ide\scratch\PSA-MT

# Create virtual environment
python -m venv .venv

# Activate virtual environment (Windows Powershell)
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### Scraping Proof of Concept

To run the proof-of-concept scraper for the Ministry of Agriculture & Livestock Development website:

```bash
python -m src.scraping.main
```
