This is the perfect time to consolidate all the moving parts into a professional, comprehensive `README.md`. A project of this complexity—spanning local AI hardware, SQLite, cloud cron jobs, and a frontend SPA—needs clear documentation so you don't forget how the pieces connect six months from now.

Here is the complete `README.md` detailing the architecture, the split local/cloud workflow, and the debugging steps.

***

```markdown
# ⚙️ Hephaestus Terminal

An institutional-grade, AI-driven supply chain mapping terminal. Hephaestus leverages local Large Language Models (LLMs) to automatically ingest financial news, corporate wikis, and SEC filings to build a proprietary graph of global hardware dependencies, uncovering downstream market signals before they are priced in.

## 🏗️ Architecture

Hephaestus operates on a **Hybrid Local-to-Cloud** architecture to eliminate API costs while maintaining high public availability.

1. **The Intelligence Miner (Local GPU):** Python scripts pull raw corporate data and feed it to a local Llama 3 model (via Ollama). A Dynamic Resolution Engine maps the LLM's extracted text (e.g., "Google") to actual market tickers (e.g., "GOOGL") using fuzzy logic and market cap weighting, saving the relationships to a local SQLite database.
2. **The Market Sync (Cloud/Local):** Standard Python scripts use the Alpaca API and YahooQuery to pull daily pricing, P/E ratios, and market caps for over 3,500 active US equities. 
3. **The Export & Dashboard (Cloud):** A shell script merges the graph data and financial metrics, exporting them to a static `dashboard_data.json` file. The frontend is a Single Page Application (SPA) hosted on GitHub Pages that visualizes the "Supply Chain X-Ray."

---

## 🚀 Getting Started

### Prerequisites
* **Python 3.10+**
* **Ollama:** Installed and running locally (e.g., `ollama run llama3`).
* **GPU:** Highly recommended (e.g., RTX 3080 Ti or better) for the `auto_discover_edges.py` mining script.
* **Alpaca API Account:** For fetching active US Equity rosters.

### 1. Secure API Key Setup
Do **NOT** hardcode your API keys. The system is designed to read them securely depending on the environment.

**Local Environment:**
Create a file at `~/.ssh/alpaca_paper_keys` formatted exactly like this:
```text
Key:YOUR_ALPACA_API_KEY
Secret_Key:YOUR_ALPACA_SECRET_KEY
```

**GitHub Actions Environment:**
Add the following to your Repository Secrets (Settings -> Secrets and variables -> Actions):
* `ALPACA_API_KEY`
* `ALPACA_SECRET_KEY`

### 2. Installation
Clone the repository and install the dependencies:
```bash
git clone [https://github.com/yourusername/Hephaestus.git](https://github.com/yourusername/Hephaestus.git)
cd Hephaestus
pip install -r requirements.txt
```

Initialize the blank SQLite database:
```bash
python3 backend/database.py
```

---

## 🧠 Running the Workflows

Hephaestus has two distinct workflows: The **Daily Sync** (which handles numbers) and the **Titan Queue** (which handles intelligence).

### Workflow 1: The Daily Sync (Automated)
This runs automatically via GitHub Actions at midnight to update stock prices and export the JSON. You can also run it manually to refresh the UI.
```bash
./run_pipeline.sh
```
*(Optional: Use `./run_pipeline.sh 10` to limit the run to 10 companies for fast debugging).*

### Workflow 2: The Titan Queue (Manual / Local GPU)
This is the proprietary extraction engine. Run this locally when you are away from your desk. It finds companies with missing data, scrapes the web, asks the LLM to extract the suppliers, and wires them into your database.
```bash
python3 backend/auto_discover_edges.py --limit 50
```
*Note: After running the Titan Queue, you must run `python3 backend/export.py` to push the newly discovered edges to the frontend JSON.*

---

## 🐛 Debugging Guide

### 1. `ConnectionRefusedError: [Errno 111] Connection refused`
* **Cause:** The Python script cannot talk to your local LLM.
* **Fix:** Ensure Ollama is running in the background. Open a terminal and run `ollama serve` or `ollama run llama3`.

### 2. The Supply Chain X-Ray is Empty in the Dashboard
* **Cause:** The `edges` table in your SQLite database is empty. The `run_pipeline.sh` script only fetches stock prices, not relationships.
* **Fix:** Run `python3 backend/seed_edges.py` to inject the hardcoded base layer, or run `python3 backend/auto_discover_edges.py` to mine new ones. Then run `python3 backend/export.py` and hard-refresh your browser.

### 3. AI Matches are saying "Filtered non-equity or private entity"
* **Cause:** The LLM successfully found a supplier (e.g., "OpenAI" or "SpaceX"), but the Dynamic Resolution Engine correctly identified that the company is not publicly traded, or the market cap is under $100M. 
* **Fix:** This is intended behavior to keep the database clean of non-tradable assets.

### 4. `GuessedAtParserWarning` from BeautifulSoup
* **Cause:** The `wikipedia` Python library uses BeautifulSoup under the hood and occasionally throws a warning about HTML parsing.
* **Fix:** Ignorable. The warning is suppressed in the latest build, but if it appears, it does not affect the data extraction.

### 5. GitHub Action Fails on `seed_db.py`
* **Cause:** The cloud server cannot authenticate with Alpaca. 
* **Fix:** Verify that `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are spelled exactly correctly in your GitHub Repository Secrets and that the keys have not been revoked in the Alpaca dashboard.

---

## 📄 License
MIT License. See `LICENSE` for more information.
```
