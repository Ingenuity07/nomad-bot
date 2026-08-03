# Nomad V3: Backend Architecture & Data Flows

This document details the backend architecture supporting **Nomad V3: Professional Knowledge Base & Deterministic Resume Operating System**. 

---

## 1. System Philosophy & Data Flow

The core backend philosophy of Nomad V3 is:
> **Strict separation of concern between Content Selection (AI-driven) and Layout Rendering (Deterministic).**

```
[Raw Resume / Input] ➔ [Resume Ingestion Engine] ➔ [Knowledge Base (DB)]
                                                    │
                                                    ▼
[Job Posting (Parsed)] ➔ [ATS Gap Analyzer] ➔ [V3 Tailor Agent]
                                                    │
                                                    ▼
                                     [Structured Resume Specification]
                                                    │
                                                    ▼
                                        [Deterministic LaTeX Engine]
                                                    │
                                                    ▼
                                           [Compiled PDF File]
```

---

## 2. Backend Components & Source Files

### A. Database Models (`memory/models.py`)
All structured data resides in the relational database. The schema consists of:
*   **`ProfessionalKnowledgeBase`**: Core profile data, summary, target roles, and years of experience.
*   **`Experience`**: Structured work history, including company, role, location, dates, tech stack, and lists of bullet points.
*   **`Project`**: Engineering projects, describing title, description, architecture details, and impact metrics.
*   **`Skill`**: Skill entities categorized (e.g., Languages, Frameworks, Databases, Cloud & DevOps) with proficiency tags.
*   **`JobPosting`**: Parsed job descriptions, capturing responsibilities, required/preferred skills, and ATS target keywords.
*   **`ResumeVersion`**: An **immutable** record of a generated resume containing the generation spec JSON, generated LaTeX code, ATS score, compilation speed, and PDF path.
*   **`ATSReport`**: Real-time evaluation of a resume version against a job posting, listing present, missing, and weak skills.
*   **`ApplicationTracker`**: Tracker mapping job postings and selected resume versions to pipeline stages (`Draft`, `Tailored`, `Applied`, `Interview`, `Rejected`, `Offer`).

### B. Ingestion & Profile Enrichment (`core/resume/ingestion.py`)
*   **How it works**: Converts unstructured resumes (PDF, Text, Markdown) into structured database records.
*   **Processing**: The engine passes raw text to the model using `IntelligentRouter`. The model formats the work history, skills, and projects into a precise JSON structure mapping to our database schema. It then creates the appropriate relational DB records.

### C. Structured Resume Specification (`core/resume/spec.py`)
*   **How it works**: Defines a strict Pydantic model (`StructuredResumeSpec`) ensuring the tailoring agent outputs valid data structures with headers, experiences, skills, and projects.

### D. Deterministic LaTeX Engine (`core/resume/latex_engine.py`)
*   **How it works**: Compiles a LaTeX resume from the Structured Spec.
*   **Escaping**: Escapes special LaTeX control characters (`%`, `$`, `&`, `_`, `#`, `{`, `}`) using regular expressions to prevent syntax compile errors.
*   **Compilation**: Compiles the LaTeX output into a PDF using tectonic/pdflatex (with a pure python text fallback if LaTeX binaries are unavailable).

### E. Job Ingestion & ATS Gap Analyzer (`core/jobs/`)
*   **`parser.py`**: Extracts required skills, responsibilities, and target ATS keywords from job URLs or raw descriptions.
*   **`ats_analyzer.py`**: Runs comparison logic between target job requirements and database skills/experiences. Computes a real-time keyword coverage score and outlines skill gaps.

### F. Honest Tailoring Agent (`core/agents/v3_tailor_agent.py`)
*   **How it works**: Custom Agent prompt that reads the parsed job description and full Knowledge Base.
*   **No Invention Rule**: The prompt forbids the LLM from fabricating skills, dates, or companies. It simply reorders experiences, highlights matching projects, and emphasizes relevant achievements already present in the user's profile.

### G. REST API Views & Routing (`api/v3_views.py` & `api/v3_urls.py`)
Exposes REST endpoints consumed by the frontend:
*   `GET /api/v3/knowledge-base/` - View the active Knowledge Base, projects, experiences, and skills.
*   `POST /api/v3/knowledge-base/ingest/` - Ingest raw resume text.
*   `POST /api/v3/jobs/parse/` - Parse raw job descriptions.
*   `POST /api/v3/resumes/tailor/` - Trigger tailoring agent and LaTeX compilation.
*   `GET /api/v3/resumes/versions/` - List all generated versions.
*   `GET /api/v3/resumes/versions/<uuid>/` - Retrieve specific version details.
*   `GET /api/v3/applications/` - Track applications and pipeline status.
