# PROJECT: Personal AI Operating System (AIOS)

You are a Principal Software Architect, Senior Python Engineer, and AI Systems Engineer.

Your task is to help me build a production-grade Personal AI Operating System (AIOS).

IMPORTANT RULES:

1. Do NOT jump directly into implementation.
2. First design architecture.
3. Follow iterative development.
4. Each phase must be independently runnable.
5. Keep code clean and production-ready.
6. Prefer maintainability over shortcuts.
7. Use SOLID principles.
8. Use Domain Driven Design where appropriate.
9. Use dependency injection and interfaces.
10. Never tightly couple the system to a specific LLM provider.

---

# PROJECT GOAL

Build a self-hosted AI Operating System that can:

* Chat with the user
* Execute tools
* Read and write files
* Use browser automation
* Access Gmail
* Access Calendar
* Manage tasks
* Research topics
* Generate reports
* Perform coding tasks
* Use multiple agents
* Maintain memory
* Switch between LLM providers

The system must work initially with Gemini CLI but be designed so that OpenAI, Claude, Grok, Ollama, or future models can be plugged in later without changing agent code.

---

# PRIMARY TECH STACK

Backend:

* Python 3.13+
* Django
* Django REST Framework
* PostgreSQL
* Redis
* Celery
* Celery Beat
* Django Channels

Frontend:

* React
* TypeScript

Infrastructure:

* Docker
* Docker Compose

Browser Automation:

* Playwright

Observability:

* Structured Logging
* OpenTelemetry

Agent Framework:

* Custom first
* LangGraph later

---

# ARCHITECTURAL REQUIREMENTS

Create the following layers:

1. API Layer
2. Agent Layer
3. Tool Layer
4. Memory Layer
5. LLM Provider Layer
6. Orchestration Layer
7. Infrastructure Layer

All layers must be independent.

Dependencies must only flow downward.

---

# LLM PROVIDER DESIGN

The system must NEVER call Gemini directly from agents.

Instead create:

BaseLLMProvider

Implementations:

* GeminiCLIProvider
* OpenAIProvider
* ClaudeProvider
* OllamaProvider

Agents should only depend on:

BaseLLMProvider

Example:

provider.generate(prompt)

Provider selection should be configurable.

Example:

MODEL_PROVIDER=gemini

Later:

MODEL_PROVIDER=openai

No code changes required.

---

# INITIAL AGENTS

Phase 1:

ResearchAgent

Capabilities:

* Ask questions
* Summarize content
* Read local files
* Search project files

Phase 2:

CodingAgent

Capabilities:

* Analyze code
* Explain code
* Generate code
* Review code

Phase 3:

BrowserAgent

Capabilities:

* Open websites
* Extract data
* Navigate pages

Phase 4:

SupervisorAgent

Responsibilities:

* Route tasks
* Delegate to agents
* Aggregate responses

---

# MEMORY SYSTEM

Design long-term memory.

Store:

* User preferences
* Projects
* Past interactions
* Agent outputs

Memory must support:

* Save
* Retrieve
* Search
* Summarize

Use PostgreSQL initially.

Design so vector database support can be added later.

---

# TOOL SYSTEM

Create a plugin architecture.

Each tool should implement:

BaseTool

Examples:

FileTool
ShellTool
GitTool
BrowserTool
GmailTool
CalendarTool

Tools must be discoverable and registerable.

Agents should never directly access tool implementations.

Use a Tool Registry.

---

# ORCHESTRATION

Initially:

Single agent execution.

Later:

Supervisor -> Multiple agents.

Example:

Research Request

Supervisor
-> ResearchAgent
-> ReportAgent

Coding Request

Supervisor
-> CodingAgent

The orchestration layer must be independent from provider implementation.

---

# DATABASE DESIGN

Design models for:

UserProfile
Conversation
Message
Memory
AgentRun
ToolExecution
TaskExecution

Provide ERD before implementation.

---

# API DESIGN

Create REST APIs for:

Conversations
Messages
Tasks
Agents
Memory
Tools

Use DRF.

Document all APIs.

---

# SECURITY REQUIREMENTS

* Secure command execution
* Tool permission system
* Agent permission system
* Audit logs
* Rate limiting
* Secret management

Never hardcode credentials.

---

# DEVELOPMENT APPROACH

Work in phases.

For every phase:

1. Explain architecture.
2. Explain design decisions.
3. Create folder structure.
4. Create models.
5. Create services.
6. Create tests.
7. Create documentation.

Do not skip tests.

---

# PHASE 1 GOAL

Deliver a minimal working system with:

* Django backend
* PostgreSQL
* GeminiCLIProvider
* ResearchAgent
* FileTool
* Memory persistence
* Chat endpoint

The result should allow:

User -> API -> ResearchAgent -> GeminiCLIProvider -> Response

Before writing code:

1. Produce complete architecture.
2. Produce folder structure.
3. Produce ERD.
4. Produce sequence diagrams.
5. Identify risks and tradeoffs.
6. Wait for approval before implementation.
