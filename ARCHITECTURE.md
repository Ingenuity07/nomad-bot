# Nomad Bot V3.5 Architecture & Technical Documentation

Welcome to the **Nomad Bot V3.5** system architecture documentation. This document outlines the modular design, core execution pipelines, fallback mechanisms, cost-control guards, and agentic loops powering the AIOS (Agentic Input Output System) workspace.

---

## Table of Contents

1. [Top-Level Architecture — The Modular Big Picture](#1-top-level-architecture--the-modular-big-picture)
2. [The Professional Resume Ingestion Pipeline (`knowledge_base`)](#2-the-professional-resume-ingestion-pipeline-knowledge_base)
3. [The Resume Tailoring & Rendering Pipeline (`resume`)](#3-the-resume-tailoring--rendering-pipeline-resume)
4. [Lead Generation & Prospecting Pipeline (`prospecting`)](#4-lead-generation--prospecting-pipeline-prospecting)
5. [The Chat & Agentic Execution Loop (`chat`)](#5-the-chat--agentic-execution-loop-chat)
    - [Context Compression (Token Saving)](#context-compression-token-saving)
    - [LoopState Stuck Detection & Intervention](#loopstate-stuck-detection--intervention)
    - [Critic Reflection Retry Loop](#critic-reflection-retry-loop)
6. [LLM Central Layer & Fallback Waterfall Routing (`llm`)](#6-llm-central-layer--fallback-waterfall-routing-llm)
7. [Database Persistence Schema & Models](#7-database-persistence-schema--models)

---

## 1. Top-Level Architecture — The Modular Big Picture

Nomad Bot V3.5 is structured as a modular Django application with six distinct apps, ensuring loose coupling and clear separations of concerns:

```
                            ┌──────────────────────────────────────┐
                            │          Django ASGI/WS Server       │
                            └──────────────────┬───────────────────┘
                                               │
               ┌───────────────────────────────┼───────────────────────────────┐
               ▼                               ▼                               ▼
     ┌───────────────────┐           ┌───────────────────┐           ┌───────────────────┐
     │      llm          │           │  knowledge_base   │           │    prospecting    │
     │  (Adapters,       │           │  (Ingestion, OCR, │           │  (Lead Discovery, │
     │   Router, Config) │           │   Parser, Models) │           │   Crawler, CRM)   │
     └─────────┬─────────┘           └─────────┬─────────┘           └─────────┬─────────┘
               │                               │                               │
               └───────────────────────┐       │       ┌───────────────────────┘
                                       ▼       ▼       ▼
                                     ┌───────────────────┐
                                     │      chat         │
                                     │  (ResearchAgent,  │
                                     │   v2_graph Loop)  │
                                     └─────────┬─────────┘
                                               │
                                               ▼
                                     ┌───────────────────┐
                                     │      resume       │
                                     │  (Tailor Agent,   │
                                     │   LaTeX Compile)  │
                                     └───────────────────┘
```

---

## 2. The Professional Resume Ingestion Pipeline (`knowledge_base`)

The ingestion pipeline handles raw document ingestion (PDF, DOCX, Markdown, Plain Text), Normalization, OCR checks, and Entity Extraction into structured database tables:

```
File Upload (PDF/DOCX) ──► Load Text ──► Searchable Check ──► OCR Fallback ──► LLM Entity Parse ──► User Profile models
```

*   **Document Loader**: Parses text content based on file headers and MIME types.
*   **OCR Layer**: Only activates if the parsed document contains no readable text, avoiding unnecessary processing latency.
*   **LLM Entity Extractor**: Uses structured prompts to parse experience, projects, skills, education, and target roles.
*   **Database Mapping**: Normalizes raw text into separate tables: `Experience`, `Project`, `Skill`, and `UserProfile`.

---

## 3. The Resume Tailoring & Rendering Pipeline (`resume`)

The resume tailoring pipeline dynamically adapts a user's master profile to match target job descriptions:

1.  **Job Parser**: Extracts key requirements and ATS keywords from raw target descriptions.
2.  **Tailor Agent**: Matches user experience highlights to target job requirements, optimizing specific achievement bullets.
3.  **Jinja2 Template Compilation**: Renders the optimized specification into compilable LaTeX.
4.  **PDF/HTML Compilers**: Executes the engine to compile target formats, generating clean PDF, HTML, or DOCX assets.

---

## 4. Lead Generation & Prospecting Pipeline (`prospecting`)

The Prospecting tool serves as an autonomous lead discovery and qualification crawler:

1.  **Business Discovery Engine**: Searches local business APIs or platforms using keyword and location filters.
2.  **Website Crawler & Contact Extractor**: Crawls discovered company websites to identify contacts, emails, and social links.
3.  **Website Analyzer**: Evaluates company websites using LLM scrapers to determine business model fits (e.g., has scheduling, has delivery) and scores leads from 0 to 10.

---

## 5. The Chat & Agentic Execution Loop (`chat`)

The `chat` app acts as the conversational orchestrator, employing a stateful agent graph (`v2_graph`) with dedicated guardrails.

### Context Compression (Token Saving)
To keep context windows clean and minimize token usage, the `ResearchAgent` automatically compresses legacy tool results that exceed 50 lines.
*   **Implementation**: `_compress_old_tool_results` in [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/chat/agents/research_agent.py#L37).
*   **How it works**: For tool results generated 2+ iterations ago, it retains only the first 20 lines and the last 10 lines of the output, replacing the middle block with an ellipsis: `... (N lines omitted, full output recorded in audit trace) ...`.

### LoopState Stuck Detection & Intervention
If the agent is stuck querying the same tool repetitively, the system intervenes to force a change of strategy.
*   **Implementation**: `LoopState` in [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/chat/agents/research_agent.py#L25).
*   **How it works**: It monitors tool names and arguments. If the same tool-argument pair is called 3 consecutive times, it halts the tool execution and injects a warning: `STUCK DETECTED: You have called the same tool multiple times... You MUST change your approach.`

### Critic Reflection Retry Loop
For critical tasks, output execution is routed through a Critic Agent node before completion.
*   **Implementation**: `v2_graph` routing in [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/chat/agents/v2_graph.py#L499).
*   **How it works**: The critic reviews the proposed plan output. If it fails, the step index is reset, and the executor node is triggered again with the critic's feedback (supporting up to 3 retries).

---

## 6. LLM Central Layer & Fallback Waterfall Routing (`llm`)

All model routing is centralized in the `llm` app, protecting the system from individual provider timeouts or outages.

*   **Complexity Scoring**: Automatically scores incoming prompts and assigns them a tier (`simple`, `medium`, `critical`).
*   **Fallback Waterfall Routing**:
    *   **Implementation**: `IntelligentRouter` in [router.py](file:///Users/shivamsingh/personal/nomad-bot/llm/router.py#L20).
    *   **How it works**: Routes are prioritized based on tier lists (`ROUTER_FALLBACK_SIMPLE`, etc.). If the primary provider (e.g. Gemini) returns an error or is blacklisted by the health monitor, the router waterfall cascades to the next healthy provider (e.g. Groq, OpenRouter, Cerebras) to process the request.
    *   **Model Locking**: Once a healthy provider successfully returns a response for a conversation, that provider is locked in the database `selected_provider` field for all subsequent turns in that thread.

---

## 7. Database Persistence Schema & Models

Persisted records are split by concern:
*   **`llm.AgentConfig`**: Stores agent prompts, temperatures, and model tiers, allowing dynamic db-driven agent behaviors.
*   **`llm.PromptRun`**: Audit logs of raw prompts, responses, tokens, and latencies.
*   **`chat.Conversation` / `chat.Message`**: Conversation state and historical logs.
*   **`chat.AgentCheckpoint`**: Serialized binary states of the LangGraph runtime, allowing humans to pause and approve/resume active runs.
*   **`chat.AgentMemory`**: Persists agent preferences (e.g., blocked companies, tech stacks).
