# AIOS: Personal AI Operating System — Base Instructions

This file serves as the system-wide developer manual and project roadmap. It preserves the requirements, architecture decisions, and task lists across sessions, ensuring that context is not lost during session compaction or restarts.

---

## 1. Core Architecture Principles

We are building a **Modular Monolith** using Clean Architecture / DDD patterns:
* **API Layer (`api/`):** Exposes DRF views. Translates HTTP requests.
* **Orchestration Layer (`orchestrator/`):** Directs the flow between APIs, database models (Memory), and AI Agents.
* **Core Agent & LLM Layer (`core/`):** Completely decoupled from Django and database libraries. Contains pure Python objects.
* **Memory Layer (`memory/`):** Manages DB-backed models (Conversations, Messages, AgentRuns, ToolExecutions).
* **Browser Automation Layer:** Integrates Playwright to interact with external websites.

---

## 2. Technical Roadmap & Phases

### Phase 1: Minimal Agentic System (Completed)
* Django backend + PostgreSQL memory persistence.
* `GeminiCLIProvider` / `GeminiAPIProvider` LLM abstraction.
* `ResearchAgent` executing a ReAct loop.
* `FileTool` registered in a dynamic `ToolRegistry`.

### Phase 2: System Expansion & Documentation Setup (Current Phase)
* Maintain a root-level `BASE_INSTRUCTIONS.md` (this file).
* Set up a `docs/articles/` directory where every architectural decision and implementation change is documented in details (What we did, Why, How, Challenges, Options, Decision context).

### Phase 3: GitHub Integration
* Implement GitHub tools using official `gh` CLI command wrappers to manage branches, search files, read diffs, and create PRs.
* Store credentials securely in the database/environment config.

### Phase 4: Playwright Browser Integration
* Set up Playwright browser instances (headed/headless support).
* Develop `BrowserTool` for HTML parsing, content extraction, capturing page screenshots (for visual verification), clicking, and form-filling.

### Phase 5: Resume & Job Reasoning Agent
* Build a specialized agent to compare job descriptions retrieved via Playwright against user profiles and Markdown-formatted resumes.
* Programmatically edit and save customized resume versions.

### Phase 6: Automated Job Applications (Human-in-the-Loop)
* Develop automation scripts/agents to navigate application forms, fill out standard text fields, upload resumes, and request human approval (via screenshots and interactive prompts) before final form submission.

### Phase 7: Celery & Task Scheduling (Headless Mode)
* Implement asynchronous tasks via Celery and RabbitMQ/Redis.
* Set up Celery Beat schedules to poll for job opportunities and trigger background application agents autonomously.

### Phase 8: V2 Durable Agent Foundation (LangGraph, Checkpointing, Memory, Planner-Executor)
* **Step 8: Environment & Durable Checkpointing:** Add LangGraph dependencies. Develop custom `DjangoCheckpointSaver` to store execution graph states in Django DB.
* **Step 9: Planner-Executor Workflow Graph:** Implement LangGraph `StateGraph` with a Planner node (generating structured JSON execution plan) and Executor node (executing registered tools). Integrate Human-in-the-Loop approval nodes.
* **Step 10: Memory Layer & API Resumption:** Implement `AgentMemory` DB models for preference storage. Add pre-run Memory injection and post-run Memory extraction nodes. Implement `/api/chat/approve/` API to resume suspended graphs.

---

## 3. General Guidelines

1. **Keep Layers Decoupled:** Never import Django models or REST API views inside `core/`.
2. **Defensive Tools:** All tool executions must return strings and handle exceptions gracefully, preventing the reasoning loop from crashing.
3. **No Placeholders:** Write fully functional, clean, and well-tested code.
4. **Documentation Sync:** For every major tool or agent introduced, write a markdown file under `docs/articles/` describing:
   * **What** we did.
   * **Why** we did it.
   * **How** we did it (with technical snippets).
   * **Challenges** faced.
   * **Options considered** and why the chosen one is best.
