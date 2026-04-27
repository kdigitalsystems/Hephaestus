That is an incredibly fitting name. The Greek god of blacksmiths, metallurgy, and the forge is the perfect mascot for a tool mapping the raw materials, foundries, and hardware that forge the modern AI industry.

Here is your updated `README.md` with **Hephaestus** fully integrated, along with the missing `backend/main.py` script to connect your scraper to your parser so the pipeline actually flows from start to finish.

### `README.md`
```markdown
# Hephaestus

An automated, AI-driven supply chain mapping tool that leverages local LLMs to track semiconductor hardware dependencies and identify downstream market signals. It exports daily relational data to a static GitHub Pages dashboard.

## Overview
The stock market efficiently prices in upstream success, but often lags on 2nd- and 3rd-degree derivatives. Hephaestus tracks those deep dependencies—from ultra-pure water and custom silicon to HBM memory and advanced packaging—to highlight the downstream suppliers poised for growth.

## Architecture
This project uses a hybrid local-to-cloud architecture to eliminate API costs while maintaining high public availability:

1. **The Extraction Engine (Local):** Python scripts scrape financial news and SEC filings. The raw text is passed to local LLMs (like Llama 3 via Ollama) running on consumer hardware (e.g., RTX 3080 Ti) to extract structured JSON supply chain relationships.
2. **The Database (Local):** Extracted nodes and edges are stored and validated in a local SQLite database managed by SQLAlchemy.
3. **The Dashboard (Cloud):** A shell script exports the graph into a `supply_chain_data.json` file and pushes it to this repository. GitHub Pages serves the `docs/index.html` file, rendering an interactive dependency web using Cytoscape.js.

## Getting Started (Local Backend)

### Prerequisites
* Python 3.10+
* [Ollama](https://ollama.com/) installed and running locally with your chosen model (e.g., `ollama run llama3`).

### Installation
1. Clone the repository:
   ```bash
   git clone [https://github.com/yourusername/Hephaestus.git](https://github.com/yourusername/Hephaestus.git)
   cd Hephaestus
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install beautifulsoup4
   ```
3. Initialize the SQLite database:
   ```bash
   python backend/database.py
   ```

### Running the Pipeline
You can trigger the export manually, or set up a cron job to run the pipeline shell script daily:
```bash
./run_pipeline.sh
```

## Viewing the Dashboard
The frontend is hosted automatically via GitHub Pages. Ensure that your repository settings have GitHub Pages enabled and pointed to the `main` branch `/docs` folder.
```

---

### `backend/main.py`
To make the `run_pipeline.sh` work, you need a script that actually glues the scraping, parsing, and database insertion together. This script pulls an article, feeds it to your GPU for extraction, and saves the results to SQLite.
