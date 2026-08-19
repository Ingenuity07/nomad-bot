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








# 🧠 Nomad Bot — Complete Architecture Documentation

> **Version:** V3.5 | **Stack:** Django 6 · LangGraph · Celery · Redis · Supabase (PostgreSQL) · SQLite (Telemetry) · Django Channels (WebSocket)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Tech Stack & Infrastructure](#3-tech-stack--infrastructure)
4. [Module Map](#4-module-map)
5. [LLM App — Deep Dive](#5-llm-app--deep-dive)
6. [Prospecting App — Deep Dive](#6-prospecting-app--deep-dive)
7. [Chat App — Deep Dive](#7-chat-app--deep-dive)
8. [Knowledge Base App](#8-knowledge-base-app)
9. [Resume App](#9-resume-app)
10. [Applications App](#10-applications-app)
11. [URL Flow Reference](#11-url-flow-reference)
12. [Data Flow Diagrams](#12-data-flow-diagrams)
13. [Database Schema Overview](#13-database-schema-overview)

---

## 1. System Overview

**Nomad Bot** is an AI-powered job-search automation and B2B sales prospecting platform. It operates as a multi-agent Django application with the following core value propositions:

| Mode | What it does |
|------|--------------|
| **Job Agent** | Autonomously searches, scrapes, tailors resumes, fills, and submits job applications |
| **Prospecting Engine** | Discovers B2B leads, enriches company data, qualifies prospects, and orchestrates outreach campaigns |

Both modes share a common **LLM layer** that provides intelligent routing, observability, cost tracking, and a prompt registry.

---

## 2. High-Level Architecture

```mermaid
graph TB
    subgraph Client["Client Layer"]
        FE["Frontend / API Client"]
        WS["WebSocket Client"]
    end

    subgraph Gateway["Django API Gateway"]
        ASGI["ASGI Server (Daphne)"]
        REST["REST API (DRF)"]
        WSS["WebSocket (Django Channels)"]
    end

    subgraph Core["Core Applications"]
        CHAT["chat\n(Agent Orchestrator)"]
        PROSP["prospecting\n(B2B Discovery Engine)"]
        LLM["llm\n(Intelligent Router + Telemetry)"]
        KB["knowledge_base\n(User Profile + Resume KB)"]
        RESUME["resume\n(Resume Builder + LaTeX)"]
        APPS["applications\n(Application Tracker)"]
    end

    subgraph Async["Async Layer"]
        CELERY["Celery Workers"]
        BEAT["Celery Beat Scheduler"]
        REDIS["Redis (Broker + Channel Layer)"]
    end

    subgraph Storage["Storage Layer"]
        SUPA["Supabase (PostgreSQL)\n[default DB]"]
        SQLITE["llm_telemetry.sqlite3\n[telemetry DB]"]
        FS["Local Filesystem\n(PDFs, Resumes)"]
    end

    subgraph LLMProviders["LLM Providers"]
        GEMINI["Gemini 2.5 Flash"]
        GROQ["Groq (Mixtral)"]
        CEREBRAS["Cerebras (Llama)"]
        OPENROUTER["OpenRouter"]
        OLLAMA["Ollama (local)"]
    end

    FE -->|HTTP| REST
    WS -->|ws://| WSS
    ASGI --> REST
    ASGI --> WSS

    REST --> CHAT
    REST --> PROSP
    REST --> KB
    REST --> RESUME
    REST --> APPS
    REST --> LLM

    CHAT --> LLM
    PROSP --> LLM
    RESUME --> LLM
    KB --> LLM

    CHAT --> CELERY
    PROSP --> CELERY
    CELERY --> REDIS
    BEAT --> REDIS
    WSS --> REDIS

    LLM --> GEMINI
    LLM --> GROQ
    LLM --> CEREBRAS
    LLM --> OPENROUTER
    LLM --> OLLAMA

    LLM --> SQLITE
    CHAT --> SUPA
    PROSP --> SUPA
    KB --> SUPA
    RESUME --> SUPA
    APPS --> SUPA
    FS --> SUPA
```

---

## 3. Tech Stack & Infrastructure

| Component | Technology |
|-----------|-----------|
| **Web Framework** | Django 6 + Django REST Framework |
| **ASGI Server** | Daphne (for WebSocket support) |
| **Agent Orchestration** | LangGraph (StateGraph Pregel) |
| **Task Queue** | Celery + Redis broker |
| **WebSocket** | Django Channels + channels-redis |
| **Primary Database** | Supabase (PostgreSQL) |
| **Telemetry Database** | SQLite (`llm_telemetry.sqlite3`) |
| **LLM Router** | Custom `IntelligentRouter` |
| **Tracing** | OpenTelemetry (OTLP exporter, optional) |
| **Resume Rendering** | LaTeX engine + Jinja2 templates |
| **Schema Docs** | drf-spectacular (OpenAPI 3) |
| **Containerization** | Docker + docker-compose |

---

## 4. Module Map

```
nomad-bot/
├── config/           ← Django project settings, URLs, ASGI/WSGI
├── llm/              ← LLM router, providers, telemetry, prompt registry
├── chat/             ← Conversational agent, LangGraph execution
├── prospecting/      ← B2B lead discovery, enrichment, outreach pipeline
├── knowledge_base/   ← User professional KB, resume ingestion, job parsing
├── resume/           ← Resume builder, LaTeX renderer, versioning
├── applications/     ← Application tracker (job applications)
└── docs/             ← Architecture & planning documents
```

---

## 5. LLM App — Deep Dive

The `llm` application is the **central nervous system** of Nomad Bot. Every call to any LLM provider goes through this layer, which provides routing, fallback, health monitoring, cost tracking, and full observability.

### 5.1 Component Architecture

```mermaid
graph TD
    CALLER["Any App\n(chat / prospecting / resume)"]

    subgraph LLMApp["llm/ — Intelligent Router Layer"]
        REGISTRY["PromptRegistry\n(llm/prompts.py)"]
        ROUTER["IntelligentRouter\n(llm/router.py)"]
        SCORER["ComplexityScorer\n(llm/scoring.py)"]
        HEALTH["ProviderHealthMonitor\n(llm/health.py)"]
        CONTEXT["LLMRequestContext\n(llm/context.py)"]
        TRACER["OTel Tracer\n(llm/tracing.py)"]

        subgraph Adapters["Provider Adapters (llm/adapters/)"]
            GA["gemini.py\nGemini 2.5 Flash"]
            GR["groq.py\nMixtral 8x7B"]
            CE["cerebras.py\nLlama 3.1-8B"]
            OR["openrouter.py\nMeta Llama 3"]
            OL["ollama.py\nQwen 3:8B (local)"]
        end

        subgraph Models["DB Models (llm/models.py)"]
            LLMPROMPT["LLMPrompt\n(versioned prompt templates)"]
            PROMPTRUN["PromptRun\n(telemetry runs — SQLite)"]
            AGENTCFG["AgentConfig\n(system prompt + tier config)"]
        end
    end

    CALLER -->|generate prompt + key| ROUTER
    ROUTER --> REGISTRY
    REGISTRY --> LLMPROMPT
    ROUTER --> SCORER
    SCORER -->|score to tier| ROUTER
    ROUTER --> HEALTH
    HEALTH -->|healthy?| ROUTER
    ROUTER --> CONTEXT
    ROUTER --> TRACER
    ROUTER --> GA
    ROUTER --> GR
    ROUTER --> CE
    ROUTER --> OR
    ROUTER --> OL
    ROUTER -->|PromptRun.create| PROMPTRUN
```

### 5.2 IntelligentRouter — Request Lifecycle

```mermaid
sequenceDiagram
    participant Caller as App (chat/prospecting)
    participant Router as IntelligentRouter
    participant Registry as PromptRegistry
    participant Scorer as ComplexityScorer
    participant Health as ProviderHealthMonitor
    participant Adapter as Provider Adapter
    participant OTel as OpenTelemetry
    participant DB as SQLite (telemetry)

    Caller->>Router: generate(prompt, prompt_key, template_vars)
    
    Note over Router,Registry: Prompt Resolution
    Router->>Registry: render(prompt_key, variables)
    Registry-->>Router: rendered_prompt + metadata

    Note over Router,Scorer: Complexity Classification
    Router->>Scorer: calculate_complexity_score(prompt, tools)
    Scorer-->>Router: score to tier (simple/medium/critical)

    Note over Router: Tier Fallback Order
    Router->>Router: Determine fallback_list for tier

    loop Provider Waterfall
        Router->>Health: is_healthy(provider)?
        alt Healthy
            Router->>OTel: start_span(operation)
            Router->>Adapter: adapter.generate(prompt, system_prompt, tools)
            Note over Adapter: Up to 3 retries for 429/5xx
            Adapter-->>Router: result (text / error)
            Router->>OTel: end_span + set attributes
            Router->>DB: PromptRun.objects.create(...)
            alt Success
                Router->>Health: report_success(provider)
                Router-->>Caller: result
            else Error
                Router->>Health: report_failure(provider, status_code)
                Note over Router: Try next provider
            end
        else Blacklisted
            Router->>Router: Skip provider
        end
    end
```

### 5.3 Complexity Scoring

The router classifies every request into a **tier** before selecting providers:

| Tier | Score Range | Priority Providers |
|------|------------|-------------------|
| `simple` | 0–5 | Groq → Ollama → Cerebras → OpenRouter → Gemini |
| `medium` | 6–12 | Groq → OpenRouter → Gemini → Ollama → Cerebras |
| `critical` | 13+ | Gemini → Groq → OpenRouter → Cerebras → Ollama |

**Scoring factors:**
- Prompt length: +3 (>1K chars), +6 (>3K), +10 (>5K)
- Keyword: `resume/cv/jd` = +5, `pdf/document` = +3, `browser/scrape` = +2
- Structure: `reflect/critic` = +4, `plan/roadmap/steps` = +6
- Tools: `BrowserTool` = +3, >3 tools = +2

### 5.4 Provider Health Monitor

`ProviderHealthMonitor` is a **singleton** that tracks provider availability with cooldown blacklisting:

```
report_failure(provider, status_code)  →  blacklist for 120 seconds
report_success(provider)               →  mark healthy
is_healthy(provider)                   →  check if cooldown expired
```

### 5.5 Prompt Registry & Versioning

```mermaid
graph LR
    subgraph DB["Supabase DB (LLMPrompt table)"]
        P1["key: router.system\nversion: 1 (active)"]
        P2["key: prospecting.intent\nversion: 2 (active)"]
        P3["key: resume.tailor\nversion: 1 (active)"]
    end

    subgraph Registry["PromptRegistry (llm/prompts.py)"]
        RENDER["render(key, variables, version)"]
        JINJA["Jinja2 Template Engine"]
    end

    APP["Any Application"] -->|prompt_key=router.system| RENDER
    RENDER --> DB
    DB --> RENDER
    RENDER --> JINJA
    JINJA -->|rendered string| APP
```

**Rules:**
- Only one version of a `key` can be `is_active=True` at a time
- Templates are **immutable** once used in a `PromptRun` (prevents history corruption)
- Templates use **Jinja2** syntax for variable injection (`{{ variable }}`)

### 5.6 Telemetry & Observability

Every LLM call persists a `PromptRun` record in the **separate SQLite telemetry database**:

| Field | Purpose |
|-------|---------|
| `provider` / `model` | Which provider was used |
| `input_tokens` / `output_tokens` | Token usage |
| `input_cost` / `output_cost` / `total_cost` | Cost in USD |
| `latency_ms` / `duration_ms` | Performance metrics |
| `trace_id` / `span_id` | OpenTelemetry trace correlation |
| `correlation_id` / `operation` | Business context |
| `prompt_key` / `prompt_version` | Prompt registry tracking |
| `status` / `error_type` / `error_code` | Error classification |
| `retry_count` | How many retries were needed |

**Analytics API:** `GET /api/llm/analytics/` returns aggregated telemetry from the SQLite database.

### 5.7 LLM API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/llm/providers/` | List all providers with health status and models |
| `GET` | `/api/llm/analytics/` | Aggregated LLM usage, cost, and latency analytics |

---

## 6. Prospecting App — Deep Dive

The `prospecting` application is the **B2B lead generation engine**. It converts a natural language objective into a full lead discovery → qualification → enrichment → outreach pipeline.

### 6.1 End-to-End Pipeline

```mermaid
graph TD
    NL["Natural Language Objective\n(user input)"] 

    subgraph Intake["Phase 1: Intent Intake and Parsing"]
        IA["POST /api/prospecting/intake/\nCreate ProspectingRequest"]
        PARSE["POST /intake/id/parse/\nIntentParser via LLM"]
        CLARIFY["POST /intake/id/clarify/\n(if ambiguous)"]
        REVIEW["GET /intake/id/specification/\nStructured Spec Preview"]
        CONFIRM["POST /intake/id/confirm/\nUser Confirms Spec"]
    end

    subgraph Discovery["Phase 2: Lead Discovery (Celery)"]
        DISCOVER["POST /api/prospecting/discover/\nKick off DiscoveryRun"]
        DPR["DiscoveryProviders:\nDuckDuckGo Search\nGoogle Places\nApify\nSocial (LinkedIn)"]
        DEDUP["Deduplicator\n(URL + domain hash)"]
        NORM["Normalizer\n(clean company records)"]
    end

    subgraph Enrichment["Phase 3: Enrichment (LangGraph)"]
        CONTACT["ContactExtractor\n(find emails/phones)"]
        ANALYZE["WebsiteAnalyzer\n(company profile via LLM)"]
        RESEARCH["ResearchGraph\n(LangGraph subgraph)"]
    end

    subgraph Qualification["Phase 4: Qualification and Scoring"]
        QUAL["qualification/\nICP scoring + fit analysis"]
        SIG["Problem Signals\n(buying intent signals)"]
        BGM["Buying Group Mapping"]
    end

    subgraph Outreach["Phase 5: Outreach and CRM"]
        EMAIL["Email Sequence Engine"]
        CRM["CRM Sync (LeadCRMSync)"]
        CAMP["Campaign Management"]
    end

    NL --> IA
    IA --> PARSE
    PARSE --> CLARIFY
    CLARIFY -->|re-parse| PARSE
    PARSE --> REVIEW
    REVIEW --> CONFIRM
    CONFIRM --> DISCOVER

    DISCOVER --> DPR
    DPR --> DEDUP
    DEDUP --> NORM
    NORM --> CONTACT
    CONTACT --> ANALYZE
    ANALYZE --> RESEARCH

    RESEARCH --> QUAL
    QUAL --> SIG
    SIG --> BGM
    BGM --> EMAIL
    EMAIL --> CRM
    CRM --> CAMP
```

### 6.2 Intent Parsing System

The intake system converts free-form natural language into a structured `ProspectingSpecification`:

```mermaid
graph LR
    RAW["Raw Objective:\nFind SaaS companies in NYC\nwith 50-200 employees..."]

    subgraph IntentService["ProspectingIntentService"]
        CREATE["create_intake_request()"]
        PARSE["parse_request()"]
        CLARIFY["clarify_request()"]
        CONFIRM["confirm_request()"]
    end

    subgraph Parser["ProspectingIntentParser"]
        LLM_CALL["LLM Call via\nIntelligentRouter"]
        EXTRACT["JSON Extraction\nFallback Logic"]
    end

    subgraph Schema["ProspectingSpecification Schema"]
        SPEC["target_industries: list\ncompany_sizes: list\ngeographies: list\nproduct_category: str\nrequired_signals: list\nnegative_signals: list\nicp_summary: str\nsearch_keywords: list"]
    end

    RAW --> CREATE
    CREATE --> PARSE
    PARSE --> LLM_CALL
    LLM_CALL --> EXTRACT
    EXTRACT --> SPEC
    SPEC -->|needs clarification| CLARIFY
    CLARIFY --> PARSE
    SPEC -->|complete| CONFIRM
```

**Request Status Machine:**
```
DRAFT → PARSING → READY_FOR_REVIEW → ACTIVE
              ↓
           FAILED
```

### 6.3 Discovery Engine

```mermaid
graph TD
    SPEC["ProspectingSpecification\n(confirmed)"]

    subgraph DiscoveryRun["DiscoveryRun (Celery Task)"]
        direction TB
        QUERY["build_duckduckgo_queries()\n3 query variants"]
        
        subgraph Providers["Discovery Providers (pluggable registry)"]
            DDG["SearchDiscoveryProvider\n(DuckDuckGo)"]
            GP["GooglePlacesProvider\n(Places API)"]
            APIFY["ApifyProvider\n(web scraping)"]
            SOC["SocialProvider\n(LinkedIn)"]
        end
        
        MERGE["Merge results\nfrom all providers"]
        DEDUP2["Deduplicator\n(URL + domain hash matching)"]
        NORM2["Normalizer\n(company name, domain, industry)"]
    end

    subgraph Progress["Real-Time Progress"]
        CACHE["Redis Cache\ndiscovery_run:{id}:progress"]
        WS2["WebSocket Broadcast\nprospecting_{run_id}"]
    end

    SPEC --> QUERY
    QUERY --> DDG
    QUERY --> GP
    QUERY --> APIFY
    QUERY --> SOC
    DDG & GP & APIFY & SOC --> MERGE
    MERGE --> DEDUP2
    DEDUP2 --> NORM2
    NORM2 -->|creates LeadCompany records| DB["Supabase DB"]
    NORM2 --> CACHE
    NORM2 --> WS2
```

### 6.4 Lead Enrichment via LangGraph Research Graph

After discovery, each lead company undergoes deep enrichment through a dedicated **LangGraph subgraph**:

```mermaid
stateDiagram-v2
    [*] --> company_research
    company_research --> contact_extraction
    contact_extraction --> website_analysis
    website_analysis --> signal_detection
    signal_detection --> buying_group_mapping
    buying_group_mapping --> [*]
```

| Node | Purpose |
|------|---------|
| `company_research` | Web search + scraping for company info |
| `contact_extraction` | Find decision-maker emails and phone numbers |
| `website_analysis` | LLM-powered analysis of company website |
| `signal_detection` | Identify buying intent signals |
| `buying_group_mapping` | Map stakeholders for multi-threaded outreach |

### 6.5 Prospecting Data Model

```mermaid
erDiagram
    Workspace ||--o{ ProspectingCampaign : has
    ProspectingCampaign ||--o| ProspectingRequest : linked_to
    ProspectingRequest ||--o{ ProspectingSpecificationVersion : versions
    ProspectingRequest ||--o{ DiscoveryRun : runs
    DiscoveryRun ||--o{ LeadCompany : discovers
    LeadCompany ||--o{ LeadContact : contacts
    LeadCompany ||--o{ LeadSignal : signals
    LeadCompany ||--o{ CompanyEvidence : evidence
    LeadCompany ||--o{ BuyingGroupMember : buying_group
    ProspectingCampaign ||--o{ CampaignEnrollment : enrolls
    CampaignEnrollment ||--o{ EmailSequence : has
    EmailSequence ||--o{ EmailMessage : contains
    EmailMessage ||--o{ InboundReply : replies
    TargetList ||--o{ LeadCompany : contains
    ProblemSignal }o--o{ LeadCompany : triggered_by
```

### 6.6 Prospecting API Endpoints (Full Reference)

**Intake (Natural Language → Specification)**

| Method | URL | Purpose |
|--------|-----|---------|
| `POST` | `/api/prospecting/intake/` | Create new prospecting request from NL objective |
| `GET/DELETE` | `/api/prospecting/intake/{id}/` | View or cancel intake request |
| `POST` | `/api/prospecting/intake/{id}/parse/` | Parse objective via LLM → structured spec |
| `POST` | `/api/prospecting/intake/{id}/clarify/` | Submit clarifications and re-parse |
| `GET` | `/api/prospecting/intake/{id}/specification/` | Preview generated specification |
| `POST` | `/api/prospecting/intake/{id}/confirm/` | Approve spec and activate campaign |
| `POST` | `/api/prospecting/intake/{id}/cancel/` | Cancel and discard intake |

**Discovery**

| Method | URL | Purpose |
|--------|-----|---------|
| `POST` | `/api/prospecting/discover/` | Kick off a discovery run (Celery async) |
| `GET` | `/api/prospecting/discover/{id}/status/` | Poll discovery run status/progress |
| `GET` | `/api/prospecting/discovery-runs/` | List all discovery runs |
| `GET` | `/api/prospecting/discovery-runs/{id}/` | Discovery run detail + metrics |
| `GET` | `/api/prospecting/discovery-runs/{id}/leads/` | Leads found in a specific run |

**Lead Management**

| Method | URL | Purpose |
|--------|-----|---------|
| `GET` | `/api/prospecting/leads/` | List all leads (with filters) |
| `GET/PATCH` | `/api/prospecting/leads/{id}/` | Lead detail + update status |
| `GET` | `/api/prospecting/leads/{id}/evidence/` | Company evidence (web scrapes) |
| `GET` | `/api/prospecting/leads/{id}/signals/` | Buying intent signals |
| `GET` | `/api/prospecting/leads/{id}/contacts/` | Decision-maker contacts |
| `GET` | `/api/prospecting/leads/{id}/buying-group/` | Buying group members |
| `POST` | `/api/prospecting/leads/{id}/research/` | Trigger deep research (LangGraph) |
| `POST` | `/api/prospecting/leads/{id}/refresh/` | Refresh lead data |
| `GET` | `/api/prospecting/leads/{id}/intelligence/` | AI-generated company intelligence |
| `GET` | `/api/prospecting/leads/{id}/sales-guidance/` | Sales approach recommendations |
| `POST` | `/api/prospecting/leads/{id}/feedback/` | Mark lead as good/bad fit |
| `POST` | `/api/prospecting/leads/{id}/sync-crm/` | Push to external CRM |

**Campaigns & Outreach**

| Method | URL | Purpose |
|--------|-----|---------|
| `GET/POST` | `/api/prospecting/campaigns/` | List/create campaigns |
| `GET/PATCH/DELETE` | `/api/prospecting/campaigns/{id}/` | Campaign detail |
| `GET` | `/api/prospecting/campaigns/{id}/leads/` | Leads in campaign |
| `POST` | `/api/prospecting/campaigns/enrollments/` | Enroll leads in campaign |
| `GET/POST` | `/api/prospecting/emails/sequences/` | Email sequences |
| `GET/POST` | `/api/prospecting/emails/messages/` | Email messages |
| `POST` | `/api/prospecting/emails/messages/{id}/send/` | Send email message |
| `GET/POST` | `/api/prospecting/emails/replies/` | Inbound reply tracking |

**Target Lists & Signals**

| Method | URL | Purpose |
|--------|-----|---------|
| `GET/POST` | `/api/prospecting/lists/` | Target list management |
| `GET/PATCH/DELETE` | `/api/prospecting/lists/{id}/` | Target list detail |
| `GET/POST` | `/api/prospecting/signals/` | Problem signals |
| `GET/PATCH/DELETE` | `/api/prospecting/signals/{id}/` | Signal detail |

**Dashboard**

| Method | URL | Purpose |
|--------|-----|---------|
| `GET` | `/api/prospecting/dashboard/overview/` | Pipeline overview metrics |
| `GET` | `/api/prospecting/dashboard/signals/` | Signal analytics |
| `GET` | `/api/prospecting/dashboard/funnel/` | Funnel conversion metrics |
| `GET` | `/api/prospecting/dashboard/opportunities/` | Hot opportunity list |

---

## 7. Chat App — Deep Dive

The `chat` application is the **conversational job automation interface**. It uses a **LangGraph-powered multi-node agent graph** to search jobs, tailor resumes, fill forms, get human approval, and submit applications.

### 7.1 Agent Graph Architecture (V2)

```mermaid
stateDiagram-v2
    [*] --> memory_injection
    memory_injection --> planner
    planner --> executor

    executor --> critic

    critic --> executor : RETRY step failed critique retries less than 3
    critic --> approval_wait : PAUSE submit_application not approved
    critic --> submit : RESUME human_approved is true
    critic --> memory_extraction : DONE all steps complete

    approval_wait --> [*]
    submit --> memory_extraction
    memory_extraction --> [*]
```

### 7.2 Agent Graph Nodes

| Node | Function | Key Logic |
|------|----------|-----------|
| `memory_injection` | Loads user's `AgentMemory` from DB | Injects as preference context |
| `planner` | Calls `PlannerAgent` via LLM | Generates ordered plan steps |
| `executor` | Runs `ResearchAgent` or `JobReasoningAgent` | Executes one step at a time |
| `critic` | Quality gate — LLM critique of output | Retry or advance decision |
| `approval_wait` | HITL checkpoint | Pauses graph, sends WebSocket event |
| `submit` | Clicks submit button via browser tool | Final form submission |
| `memory_extraction` | Extracts preferences from conversation | Saves to `AgentMemory` table |

### 7.3 Plan Steps

The planner generates a sequence of atomic plan steps:

| Step | Agent | Description |
|------|-------|-------------|
| `search_jobs` | ResearchAgent | Search job boards for matching roles |
| `scrape_job` | ResearchAgent | Navigate to job URL and scrape details |
| `tailor_resume` | JobReasoningAgent | Customize resume to match JD |
| `fill_application` | JobReasoningAgent | Fill application form via browser |
| `submit_application` | ResearchAgent | Click submit (requires human approval) |
| `general_task` | ResearchAgent | Generic free-form task |

### 7.4 Checkpoint Persistence (LangGraph)

State is persisted between turns using `DjangoCheckpointSaver`:

```mermaid
graph LR
    GRAPH["LangGraph Pregel"] -->|put checkpoint| SAVER["DjangoCheckpointSaver"]
    SAVER -->|serialize state| RLOCK["threading.RLock\n(concurrency guard)"]
    RLOCK -->|write| SUPA["Supabase DB\nCheckpoint table"]
    GRAPH -->|get checkpoint| SAVER
    SAVER -->|read| SUPA
```

**Conversation locking** is applied at both Python-level (`threading.RLock`) and database-level to prevent concurrent writes from parallel Pregel graph execution.

### 7.5 Real-Time WebSocket Events

The agent broadcasts live updates during execution via **Django Channels**:

| Event Type | When Triggered |
|-----------|----------------|
| `planner_start` | Planner begins |
| `planner_plan` | Plan generated |
| `executor_step_start` | Step begins executing |
| `executor_step_end` | Step completes |
| `executor_step_failed` | Step throws exception |
| `tool_execution` | Any tool call (search, browser, etc.) |
| `critic_start` | Critic begins |
| `critic_pass` | Step passes critique |
| `critic_fail` | Step fails critique (retry) |
| `critic_max_retries` | Max retries reached |
| `approval_requested` | HITL approval needed |
| `submit_start` | Submission begins |
| `submit_end` | Submission success |
| `submit_failed` | Submission error |

WebSocket channels are keyed as `chat_{conversation_id}`.

### 7.6 Conversation Provider Locking

Each `Conversation` record stores the currently active LLM provider:
```
Conversation.selected_provider  →  "gemini-flash" | "groq" | ...
Conversation.selected_model     →  actual model name string
```
Once a provider is selected for a conversation, subsequent turns use the **same provider** until it fails health checks, at which point the router automatically fails over and updates the lock.

### 7.7 Chat API Endpoints

| Method | URL | Purpose |
|--------|-----|---------|
| `POST` | `/api/chat/chat/` | Send a chat message (triggers agent graph) |
| `POST` | `/api/chat/chat/approve/` | Approve pending form submission |
| `GET` | `/api/chat/conversations/` | List all conversations |
| `GET/DELETE` | `/api/chat/conversations/{id}/` | Conversation detail or delete |

**WebSocket:** `ws://<host>/ws/chat/{conversation_id}/`

---

## 8. Knowledge Base App

The `knowledge_base` module manages the **user's professional identity and job intelligence**.

### 8.1 Architecture

```
knowledge_base/
├── models.py          ← UserProfile, ProfessionalKnowledgeBase, Experience, Project, Skill, JobPosting
├── ingestion.py       ← ResumeIngestionEngine (PDF/DOCX → structured KB)
├── views.py           ← REST API views
├── urls.py            ← URL routes
├── documents/         ← Document parsing utilities
└── jobs/
    ├── parser.py      ← JobParser (JD → structured data)
    └── ats_analyzer.py← ATSGapAnalyzer (resume vs JD gap analysis)
```

### 8.2 Core Models

| Model | Description |
|-------|-------------|
| `UserProfile` | Central user identity record |
| `ProfessionalKnowledgeBase` | Summary, headline, years of experience |
| `Experience` | Work history with bullet points and tech stack |
| `Project` | Portfolio projects with impact metrics |
| `Skill` | Skills by category (programming, tools, soft skills) |
| `JobPosting` | Saved/analyzed job postings |

### 8.3 Knowledge Base API Endpoints

| Method | URL | Purpose |
|--------|-----|---------|
| `GET/POST` | `/api/kb/` | View/update professional KB |
| `POST` | `/api/kb/ingest/resume/` | Parse raw resume text or PDF → KB entities |
| `POST` | `/api/kb/jobs/parse/` | Parse job description → structured JobPosting |
| `GET` | `/api/kb/jobs/ats/` | ATS gap analysis (resume vs JD) |

---

## 9. Resume App

The `resume` application handles **resume generation, versioning, and rendering**.

### 9.1 Architecture

```
resume/
├── models.py          ← ResumeVersion (versioned resume states)
├── templates.py       ← Template library for different resume styles
├── latex_engine.py    ← LaTeX PDF rendering pipeline
├── diff.py            ← Diff engine for version comparison
├── spec.py            ← Resume specification schema
├── ingestion.py       ← Shared resume ingestion logic
├── agents/            ← AI resume agents
├── pipelines/         ← Multi-step resume generation pipelines
└── renderers/         ← Multiple output format renderers
```

### 9.2 Resume Generation Flow

```mermaid
graph LR
    JD["Job Description"] --> TAILOR["AI Tailoring\n(JobReasoningAgent)"]
    KB["User Knowledge Base"] --> TAILOR
    TAILOR --> SPEC["Resume Spec\n(structured JSON)"]
    SPEC --> LATEX["LaTeX Engine\n(Jinja2 → .tex → PDF)"]
    SPEC --> MD["Markdown Renderer"]
    LATEX --> PDF["PDF Output"]
    MD --> MDF["Markdown File"]
    PDF & MDF --> VERSION["ResumeVersion\n(versioned record)"]
```

### 9.3 Resume API Endpoints

| Method | URL | Purpose |
|--------|-----|---------|
| `GET/POST` | `/api/resume/` | List versions / create new resume |
| `GET/PATCH` | `/api/resume/{id}/` | Resume version detail |
| `POST` | `/api/resume/{id}/tailor/` | AI-tailor resume to job description |
| `GET` | `/api/resume/{id}/render/` | Render to PDF/LaTeX |
| `GET` | `/api/resume/{id}/diff/` | Diff two resume versions |

---

## 10. Applications App

The `applications` application tracks the **job application lifecycle**.

### 10.1 Core Model

| Model | Fields |
|-------|--------|
| `ApplicationTracker` | `job_title`, `company`, `url`, `status`, `resume_version`, `applied_at`, `notes` |

### 10.2 Application API Endpoints

| Method | URL | Purpose |
|--------|-----|---------|
| `GET/POST` | `/api/applications/` | List / create applications |

---

## 11. URL Flow Reference

### Global URL Configuration

```
/api/llm/          →  llm/urls.py
/api/chat/         →  chat/urls.py
/api/prospecting/  →  prospecting/urls.py
/api/kb/           →  knowledge_base/urls.py
/api/resume/       →  resume/urls.py
/api/applications/ →  applications/urls.py
/admin/            →  Django Admin
/api/schema/       →  OpenAPI 3 schema (drf-spectacular)
/api/docs/         →  Swagger UI
```

---

## 12. Data Flow Diagrams

### 12.1 Chat Job Application Flow (End-to-End)

```mermaid
sequenceDiagram
    actor User
    participant API as Chat API
    participant Graph as LangGraph V2 Graph
    participant Planner
    participant Executor
    participant Critic
    participant LLMRouter as IntelligentRouter
    participant Tools as Tool System
    participant WS as WebSocket

    User->>API: POST /api/chat/chat/
    API->>Graph: stream_graph(state)
    Graph->>WS: memory_injection loaded memories

    Graph->>Planner: planner_node
    Planner->>LLMRouter: generate plan prompt
    LLMRouter-->>Planner: search_jobs scrape_job tailor_resume fill_application submit_application
    Graph->>WS: planner_plan event

    loop For each plan step
        Graph->>Executor: executor_node(step)
        Executor->>LLMRouter: generate(step_prompt)
        LLMRouter-->>Executor: tool_calls
        Executor->>Tools: execute tool
        Tools-->>Executor: tool result
        Graph->>WS: executor_step_end event

        Graph->>Critic: critic_node
        Critic->>LLMRouter: generate critique prompt
        LLMRouter-->>Critic: success true or false
        
        alt Step failed
            Graph->>WS: critic_fail event
            Graph->>Executor: retry step
        else Step passed
            Graph->>WS: critic_pass event
        end
    end

    Graph->>WS: approval_requested event
    User->>API: POST /api/chat/chat/approve/
    Graph->>Executor: submit_node
    Graph->>WS: submit_end event
    Graph->>Graph: memory_extraction_node
    API-->>User: status complete
```

### 12.2 Prospecting Intent to Discovery Flow

```mermaid
sequenceDiagram
    actor User
    participant API as Prospecting API
    participant IntentSvc as IntentService
    participant Parser as IntentParser
    participant LLM as IntelligentRouter
    participant Celery as Celery Worker
    participant Discovery as DiscoveryEngine
    participant WS as WebSocket

    User->>API: POST /api/prospecting/intake/
    API->>IntentSvc: create_intake_request(objective)
    IntentSvc-->>User: id and status DRAFT

    User->>API: POST /api/prospecting/intake/id/parse/
    API->>IntentSvc: parse_request(id)
    IntentSvc->>Parser: parse_intent(objective, target, qualification)
    Parser->>LLM: generate(intent_prompt, template_vars)
    LLM-->>Parser: JSON specification
    Parser-->>IntentSvc: ProspectingSpecification
    IntentSvc-->>User: status READY_FOR_REVIEW with specification

    User->>API: POST /api/prospecting/intake/id/confirm/
    API->>IntentSvc: confirm_request(id)
    IntentSvc->>Celery: discover_campaign_async.delay(run_id)
    IntentSvc-->>User: status ACTIVE

    Celery->>Discovery: run DiscoveryEngine
    Discovery->>WS: broadcast_progress searching 10 percent
    Discovery->>Discovery: DuckDuckGo queries Normalize Deduplicate
    Discovery->>WS: broadcast_progress enriching 60 percent
    Discovery->>Discovery: ContactExtractor + WebsiteAnalyzer
    Discovery->>WS: broadcast_completion discovered new duplicates
```

### 12.3 LLM Provider Waterfall (Failure Scenario)

```mermaid
sequenceDiagram
    participant R as IntelligentRouter
    participant H as HealthMonitor
    participant G as Groq (primary)
    participant GF as Gemini Flash (fallback)
    participant DB as SQLite Telemetry

    R->>H: is_healthy groq? → true
    R->>G: adapter.generate(prompt)
    G-->>R: error status_code 429
    R->>H: report_failure groq 429
    R->>DB: PromptRun status=error error_type=RATE_LIMIT

    R->>H: is_healthy gemini-flash? → true
    R->>GF: adapter.generate(prompt)
    GF-->>R: type=text result
    R->>H: report_success gemini-flash
    R->>DB: PromptRun status=success
    R-->>R: Update Conversation.selected_provider = gemini-flash
```

---

## 13. Database Schema Overview

### 13.1 Primary Database (Supabase / PostgreSQL)

**`llm` app tables:**
- `llm_llmprompt` — versioned prompt templates
- `llm_agentconfig` — agent system prompts and tier configuration

**`chat` app tables:**
- `chat_conversation` — conversation sessions with locked provider
- `chat_message` — individual messages
- `chat_agentrun` — LangGraph execution run records
- `chat_toolexecution` — per-tool execution logs
- `chat_agentmemory` — long-term user memory (preferences, profile facts)
- `chat_checkpoint` — LangGraph state checkpoints

**`prospecting` app tables:**
- `prospecting_workspace` — multi-tenant workspace
- `prospecting_prospectingcampaign` — campaign lifecycle
- `prospecting_icpprofile` — ideal customer profile
- `prospecting_prospectingrequest` — NL intake request
- `prospecting_prospectingspecificationversion` — immutable parsed specs
- `prospecting_discoveryrun` — discovery run records
- `prospecting_leadcompany` — discovered companies
- `prospecting_leadcontact` — decision-maker contacts
- `prospecting_leadsignal` — buying intent signals
- `prospecting_companyevidence` — web scrape evidence
- `prospecting_buyinggroupmember` — stakeholder mapping
- `prospecting_targetlist` — curated lead lists
- `prospecting_campaignenrollment` — campaign to lead join
- `prospecting_emailsequence` — outreach email sequences
- `prospecting_emailmessage` — individual emails
- `prospecting_inboundreply` — reply tracking

**`knowledge_base` app tables:**
- `knowledge_base_userprofile` — user identity
- `knowledge_base_professionalkb` — professional summary
- `knowledge_base_experience` — work history
- `knowledge_base_project` — portfolio projects
- `knowledge_base_skill` — skill inventory
- `knowledge_base_jobposting` — analyzed job postings

**`resume` app tables:**
- `resume_resumeversion` — versioned resume states

**`applications` app tables:**
- `applications_applicationtracker` — job application records

### 13.2 Telemetry Database (SQLite — `llm_telemetry.sqlite3`)

> Isolated from primary DB. Uses WAL mode for concurrent read-write access.

- `llm_promptrun` — every LLM call with full observability metadata

---

## Appendix: Environment Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `GEMINI_API_KEY` | Gemini provider key | — |
| `GROQ_API_KEY` | Groq provider key | — |
| `CEREBRAS_API_KEY` | Cerebras provider key | — |
| `OPENROUTER_API_KEY` | OpenRouter key | — |
| `GEMINI_MODEL` | Gemini model override | `gemini-2.5-flash` |
| `GROQ_MODEL` | Groq model override | `mixtral-8x7b-32768` |
| `CEREBRAS_MODEL` | Cerebras model override | `llama3.1-8b` |
| `OPENROUTER_MODEL` | OpenRouter model override | `meta-llama/llama-3-8b-instruct:free` |
| `OLLAMA_MODEL` | Ollama local model | `qwen3:8b` |
| `ROUTER_PROVIDER_PRIORITY` | Global provider order | — |
| `ROUTER_FALLBACK_SIMPLE` | Simple tier order | `groq,ollama,cerebras,openrouter,gemini-flash` |
| `ROUTER_FALLBACK_MEDIUM` | Medium tier order | `groq,openrouter,gemini-flash,ollama,cerebras` |
| `ROUTER_FALLBACK_CRITICAL` | Critical tier order | `gemini-flash,groq,openrouter,cerebras,ollama` |
| `REDIS_HOST` | Redis broker host | `127.0.0.1` |
| `OTEL_ENABLED` | Enable OTel tracing | `false` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP endpoint | — |
| `LLM_TRACING_ENABLED` | Enable LLM tracing | `true` |
