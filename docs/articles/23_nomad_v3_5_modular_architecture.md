# Nomad V3.5: Modular Ingestion, Database Templates, and Optimization Loop

This document outlines the architectural upgrades introduced in **Nomad V3.5**.

---

## 1. Modular Document Ingestion Pipeline (`core/documents/`)

Instead of parsing resumes in a single, high-token LLM call, V3.5 implements a multi-stage document processing pipeline:

1.  **`DocumentLoader` (`loader.py`)**: Support loading PDF, DOCX, Markdown, and text files. Extracts text using specialized libraries like `pypdf` and `python-docx`.
2.  **`OCRService` (`ocr.py`)**: Detects if a document contains searchable text. If it is a scanned image or non-searchable PDF, runs fallback OCR.
3.  **`SectionDetector` (`detector.py`)**: Analyzes document layout and segments raw text into distinct sections (Summary, Experience, Projects, Skills, Education).
4.  **`ResumeIngestionEngine` (`core/resume/ingestion.py`)**: Extracts structured entities section-by-section to reduce token cost and improve validation accuracy.

---

## 2. Dynamic Templates-as-Data System (`core/resume/templates.py`)

Rather than maintaining hardcoded LaTeX strings in python source code, V3.5 moves templates to a database model:

*   **`ResumeTemplate` Model**: Stores the LaTeX layout structure containing Jinja2 delimiters (`((( variable )))` and `((% for %))`).
*   **`ResumeTemplateRenderer`**: Deep escapes raw data before compilation, rendering spec data into compile-ready LaTeX source dynamically loaded from the database.

---

## 3. Granular ATS Scorer & Feedback Loop (`core/pipelines/`)

The resume tailoring engine now uses a closed feedback loop:

1.  **Tailor Agent** generates the initial `StructuredResumeSpec`.
2.  **`ATSGapAnalyzer`** evaluates the spec across 4 dimensions: keyword match, verb strength, readability, and formatting.
3.  If the score is below 90%, **`BulletOptimizerAgent`** rewrites weak bullet points using target missing keywords (preserving factual accuracy).
4.  If the re-evaluated score increases, the optimized resume version is compiled and saved; otherwise, the engine rolls back to the initial tailored spec.
