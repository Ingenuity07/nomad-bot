# Nomad V3: Professional Knowledge Base & Deterministic Resume Operating System

Nomad V3 transforms Nomad Bot from a simple resume editing/job search bot into a **Personal Career Operating System**.

---

## 1. The Core Paradigm Shift

### Old Paradigm (V2)
```
Nomad Bot ➔ Search Jobs ➔ Edit/Rewrite Resume PDF ➔ Apply
```

### New Core Operating System Paradigm (V3)
```
Professional Knowledge Base (Profile, Experiences, Projects, Skills, Achievements)
        │
┌───────┴────────┐
│                │
 Career Profile   Resume Engine
│                │
└───────┬────────┘
        │
Job Matching Engine
        │
Resume Tailoring Engine
        │
ATS Evaluation
        │
Resume Version Store
        │
Application Tracker
```

---

## 2. Fundamental Architectural Rule

> **Never let the LLM edit the resume or LaTeX directly.**  
> The pipeline strictly enforces:  
> **Professional Knowledge Base ➔ Structured Resume Specification (LLM) ➔ Deterministic LaTeX Generator ➔ PDF**  
> The LLM's responsibility is to decide **what** to include and **how to prioritize/order** bullets. A deterministic renderer handles **how** it appears.

---

## 3. Core Engine Components

1. **Professional Knowledge Base (`memory/models.py`):**
   * PostgreSQL entities: `ProfessionalKnowledgeBase`, `Experience`, `Project`, `Skill`, `JobPosting`, `ResumeVersion`, `ATSReport`, `ApplicationTracker`.
2. **Resume Ingestion Engine (`core/resume/ingestion.py`):**
   * Parses raw Markdown, PDF, or text resumes into normalized Knowledge Base database entries.
3. **Structured Spec & LaTeX Engine (`core/resume/latex_engine.py`):**
   * Safe character escaping (`%`, `$`, `&`, `_`, `#`, `{`, `}`) to guarantee syntax error-free rendering.
   * Deterministic compilation from `StructuredResumeSpec` JSON into `.tex` and `.pdf`.
4. **ATS Gap Analyzer (`core/jobs/ats_analyzer.py`):**
   * Compares Job Postings against the Knowledge Base to report match scores (%), present skills, missing skills, weak skills, and keyword optimization recommendations.
5. **V3 Tailoring Agent (`core/agents/v3_tailor_agent.py`):**
   * Analyzes target JDs and extracts relevant facts from the Knowledge Base without fabricating info.
6. **V3 Tabbed Dashboard UI (`App.tsx` & `index.css`):**
   * Top navigation bar exposing **Agent Workspace**, **Knowledge Base**, **ATS Tailor & LaTeX Engine**, and **Versions & Tracker**.
