<<<<<<< HEAD
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
=======
# Kenya agriculture PSA corpus collector

This collector creates a CSV corpus for English ↔ Swahili agricultural public-service and extension material. It is deliberately conservative:

- only sources with stated reuse terms in `sources.json` are considered;
- the source licence page is checked at runtime, and per-page-licenced sources require an explicit licence statement on each page;
- `robots.txt` is fetched first and any source whose robots file is unavailable is skipped;
- requests are throttled to one per host every 1.5 seconds by default;
- it stores provenance and licence fields on every record and does not claim that linked pages are sentence-aligned translations.

## Run a small pilot

Replace the contact portion of the user agent with a monitored address, then run:

```powershell
python .\scrape_agri_psa.py --source infonet_biovision --max-pages-per-source 25 --user-agent "KenyaAgriPSACorpusBot/0.1 (research contact: your-email@example.org)"
```

The first run produces:

- `data/kenya_agri_psa.csv`
- `data/collection_report.json`

Repeat the command to resume. Existing IDs and same-language duplicate PSA texts are not written twice. For a larger, polite run after examining the pilot:

```powershell
python .\scrape_agri_psa.py --max-pages-per-source 1500 --delay 1.5 --user-agent "KenyaAgriPSACorpusBot/0.1 (research contact: your-email@example.org)"
```

If your managed Windows network intercepts HTTPS and Python reports a certificate-verification error, add `--use-windows-root-certificates`. This creates a temporary CA bundle from Windows' trusted-root stores; it does **not** disable TLS verification.

For the quickest possible pilot, add `--max-sitemaps 0` to start from the configured seed pages only. The default reads at most four sitemap files per source; it is deliberately bounded so sitemap discovery cannot consume a collection run.

Use `--strict-psa` if only pages that have an explicit announcement/advisory signal should be retained. Without it, practical agriculture extension guidance is also retained; this is usually useful for an agricultural PSA MT domain but should be reviewed before training.

## CSV columns

The requested columns are always present:

| Column | Meaning |
| --- | --- |
| `ID` | Stable ID derived from the canonical source URL, language, and PSA text. |
| `Domain` | Domain that supplied the text. |
| `Language` | `English` or `Swahili`. |
| `PSA` | One extracted, de-duplicated text block. |

Additional fields preserve source URL, publisher, collection time, published date (when offered), licence evidence, and a `Parallel_Group_ID` where the website exposes an English/Swahili alternate link. A group ID marks a **candidate document-level parallel pair only**. It is not evidence that the extracted paragraphs are aligned translations.

`Review_Status` is intentionally conservative. It means the source and reuse terms passed automatic checks; it does not replace a human review of PSA relevance, licence restrictions (especially non-commercial/share-alike licences), or MT alignment.

## Source policy

The configuration begins with:

- **Infonet Biovision** — Kenya-focused extension resource that says its content is under a Creative Commons licence. The current wording does not name the variant, so confirm it before redistributing a release.
- **ILRI** — international agricultural research NGO with a site-wide CC BY 4.0 permissions statement. The crawler keeps Kenya-context material only.
- **KilimoSTAT** — a Kenyan government agriculture statistics site with CC BY-NC-SA 3.0 IGO terms and both English and Swahili pages. Its non-commercial and share-alike terms must remain attached to downstream use.
- **CIFOR-ICRAF Knowledge** — Kenya-active NGO; pages are accepted only if the individual publication page explicitly states a Creative Commons licence.

The Ministry of Agriculture's ordinary news site is intentionally not configured because I did not find explicit reuse terms for it. Add a source only after recording its organisation type, Kenya relationship, licence URL, licence wording to verify, seeds, and path restrictions in `sources.json`.

## Operational notes

Do not use the data for safety-critical farm guidance without checking the original source. The collector is for corpus construction, not a live advisory system. Keep the CSV's `Licence`, `Licence_URL`, and `Source_URL` columns in any derivative dataset so each record remains auditable.
>>>>>>> 0c12d18 (Initial files)
