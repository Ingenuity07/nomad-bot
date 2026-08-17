# Nomad-Bot: AI Career & Prospecting Assistant (V3.5)

Nomad-Bot is a modular, multi-tier agentic system designed to automate professional career workflows and operational lead generation. It combines advanced PDF/LaTeX resume engineering with a parallel suitability qualifier and outreach prospecting CRM.

---

## 🛠️ Architecture & Tech Stack

*   **Backend**: Python 3.14+, Django 5.x (REST APIs & WebSocket Consumers), Django Channels.
*   **Frontend**: React 18, TypeScript, Vite, Vanilla HSL design system.
*   **Database & Memory**: PostgreSQL (Data persistence), Redis (Channel layers for WebSockets).
*   **LLM Providers & Router**: Google Gemini 2.5 (Primary), Groq, Cerebras, OpenRouter, and Ollama. Incorporates a dynamic waterfall router for error-resilient fallbacks.
*   **Web Crawling**: BeautifulSoup4, public OpenStreetMap Nominatim APIs, and DuckDuckGo HTML parsers.

---

## ✨ Core Features

1.  **Resume Ingestion & Parsing**: Parses existing LaTeX resumes and extracts structured metadata (Skills, Experience, Projects).
2.  **Job Specification ATS Scoring**: Evaluates candidate resumes against target job specifications using LLM qualification rules.
3.  **Git-Style CV Spec Diffing**: Shows line-by-line and bullet-by-bullet Git diff comparisons between original and optimized resume versions.
4.  **Lead Discovery CRM & Prospecting**:
    *   **5-Way Parallel Aggregation**: Runs concurrent DuckDuckGo queries (Direct, Contact-focused, Directory listings, Reddit Intent, and GitHub organization scans).
    *   **Prioritized Contact Scraper**: Crawls up to 8 subpages, prioritizing links with contact keywords to extract emails and LinkedIn company URLs.
    *   **Intelligent Scoring**: Assesses suitability (1-10) using LLM qualification prompts.
5.  **Interactive Knowledge Base**: Dynamic forms for manually enriching skills, projects, and career history directly in the UI.

---

## 🚀 Getting Started

### 📋 Prerequisites

Ensure you have the following installed:
*   [Python 3.10+](https://www.python.org/downloads/) (Python 3.14 recommended)
*   [Node.js (v18+)](https://nodejs.org/) and `npm`
*   [Docker](https://www.docker.com/) (for running PostgreSQL and Redis)

---

### ⚙️ Installation & Setup

#### 1. Clone the Repository
```bash
git clone https://github.com/Ingenuity07/nomad-bot.git
cd nomad-bot
```

#### 2. Configure Environment Variables
Copy the template configuration file to `.env`:
```bash
cp env.example .env
```
Open `.env` and fill in your details:
*   Add your **Gemini API Key** (Primary LLM).
*   Add optional API keys for fallback providers (Groq, Cerebras, OpenRouter).
*   Modify the PostgreSQL database password or port if necessary.

#### 3. Spin up Infrastructure (Database & Redis)
Run the Docker Compose file to start PostgreSQL (port `5433`) and Redis:
```bash
docker-compose up -d
```

#### 4. Configure Virtual Environment & Python Dependencies
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

#### 5. Apply Database Migrations
```bash
python manage.py migrate
```

#### 6. Setup React Frontend
Open a new terminal window:
```bash
cd frontend
npm install
```

---

## 🏃 Running the Applications

### Start the Django Backend Server
With your virtual environment active in the root folder, run:
```bash
python manage.py runserver 8000
```
*Backend API will run at `http://localhost:8000`*

### Start the Vite Frontend Server
Inside the `frontend/` directory, run:
```bash
npm run dev
```
*Frontend interface will run at `http://localhost:5173` (or `http://localhost:5174` depending on port availability)*

---

## 🧪 Running Tests

Validate that the setup is fully correct by executing the test suite:
```bash
python manage.py test api.v3_tests
```


wsl -d Ubuntu

python -m celery -A config worker --loglevel=INFO^C      
python -m celery -A config beat --loglevel=INFO^C      