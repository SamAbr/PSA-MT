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

The scraper pipeline components have been converted into Jupyter Notebooks (`.ipynb`) located in `src/scraping/`:

- **[fetch.ipynb](file:///C:/Users/Admin/.gemini/antigravity-ide/scratch/PSA-MT/src/scraping/fetch.ipynb)**: Implements raw HTML downloading with fallbacks.
- **[parser.ipynb](file:///C:/Users/Admin/.gemini/antigravity-ide/scratch/PSA-MT/src/scraping/parser.ipynb)**: Extracts title, headings, paragraphs, and list items in document order.
- **[exporter.ipynb](file:///C:/Users/Admin/.gemini/antigravity-ide/scratch/PSA-MT/src/scraping/exporter.ipynb)**: Handles saving the scraped data into a structured CSV format.
- **[main.ipynb](file:///C:/Users/Admin/.gemini/antigravity-ide/scratch/PSA-MT/src/scraping/main.ipynb)**: Entry point notebook to configure arguments, execute, and verify the pipeline.

To run the notebooks, install Jupyter inside the virtual environment and launch the interface:

```bash
# Install Jupyter
pip install jupyter

# Start notebook interface
jupyter notebook
```
You can then open and run the cells inside `src/scraping/main.ipynb` from the browser or directly in your IDE.
