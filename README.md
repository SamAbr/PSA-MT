# PSA-MT: Public Service Announcement Machine Translation

This project focuses on crawling, preprocessing, and aligning public service announcements (PSAs) from official sources (such as government websites) to create a parallel corpus for English-Kiswahili Machine Translation.

## Directory Structure

```
PSA-MT/
├── app/                      # Streamlit application
├── data/
│   ├── raw/
│   │   ├── lecturer/         # Original lecturer dataset
│   │   ├── scraped/          # Raw scraped CSV/JSON files (e.g. agriculture.csv)
│   │   └── public_corpus/    # Downloaded public bilingual corpora
│   ├── interim/              # Merged datasets before final cleaning
│   ├── processed/            # Cleaned datasets ready for training
│   └── final/                # Final train/validation/test datasets
├── models/                   # Fine-tuned models and checkpoints
├── notebooks/                # Data exploration and experiments
├── reports/                  # Project reports, figures, evaluation results
├── src/
│   ├── scraping/             # Dedicated scraper scripts
│   │   ├── scraper_agriculture.py
│   │   ├── scraper_health.py
│   │   ├── scraper_education.py
│   │   ├── scraper_governance.py
│   │   └── scraper_security.py
│   ├── preprocessing/        # Dataset cleaning and filtering
│   │   ├── preprocess.py
│   │   ├── clean.py
│   │   └── merge.py
│   ├── training/             # MT training scripts
│   │   └── train.py
│   ├── evaluation/           # Evaluation metrics (BLEU, COMET)
│   │   └── evaluate.py
│   ├── inference/            # Prediction/inference class
│   │   └── predict.py
│   └── utils/                # Config parameters and helper utilities
│       ├── config.py
│       └── helpers.py
├── requirements.txt          # Project dependencies
└── README.md                 # Project documentation
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

### Running Scrapers

Each scraper is self-contained and pre-configured to output to its respective domain CSV file under `data/raw/scraped/`.

For example, to run the agriculture scraper against the default Ministry website (or custom URL):

```bash
# Default live site
python src/scraping/scraper_agriculture.py

# Custom target URL
python src/scraping/scraper_agriculture.py --url https://www.kilimo.go.ke

# Offline local file testing
python src/scraping/scraper_agriculture.py --url file:///C:/Users/Admin/.gemini/antigravity-ide/scratch/PSA-MT/data/raw/kilimo_mock.html
```

The output will be saved directly to `data/raw/scraped/agriculture.csv`.
