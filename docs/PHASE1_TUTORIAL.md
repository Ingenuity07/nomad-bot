# Welcome to the AIOS Project: Phase 1 Tutorial

Welcome, developer! If you're reading this, you are looking at the foundational architecture of our Personal AI Operating System (AIOS). My goal in this tutorial is to walk you through **what** we built, **why** we built it this way, the **alternatives** we considered, and **how** it all works together. 

Grab a cup of coffee. We are going to dive deep into building production-grade AI systems!

---

## 1. The Big Picture: Why Layered Architecture?

When building software, especially AI systems that evolve rapidly, the biggest enemy is **tight coupling** (when one part of your code is deeply tangled with another).

**What we did:**
We split the project into distinct, independent layers:
- `core/`: Contains the pure AI logic (LLMs, Agents, Tools).
- `memory/`: Contains the database models (Django ORM).
- `orchestrator/`: The glue that connects the API, the Database, and the AI Agents.
- `api/`: The web interface (Django REST Framework) that talks to the outside world.

**Why we did it:**
Dependencies only flow downwards. The `core` layer has zero knowledge of Django, HTTP requests, or SQL databases. It only knows about Python objects. 

**Alternatives Considered:**
*Spaghetti Architecture:* We could have written a single Django View that connects to Gemini, reads files, and saves to the database all in one 300-line function.
*Why we rejected it:* If we want to switch from Gemini to OpenAI later, or change our database from PostgreSQL to MongoDB, we would have to rewrite the entire application. With our layered approach, we just swap out a single file.

---

## 2. The Core Layer: Abstraction is Key

Let's look inside the `core/` directory.

### A. LLM Providers (`core/llm_providers/`)
**What we did:** 
We created an interface called `BaseLLMProvider`. Any AI model we use in the future must implement a `generate(prompt, system_prompt, tools)` method. Then, we created `GeminiCLIProvider` which implements this interface by making a subprocess call to a local `gemini` CLI tool.

**Why we did it:**
LLM APIs change constantly. By forcing our agents to only talk to `BaseLLMProvider`, the agents don't care if the answer comes from Google, OpenAI, or a local model.

**Alternatives:**
*Using an SDK directly (e.g., `import google.generativeai` inside the agent).* 
*Why we rejected it:* This violates the Dependency Inversion Principle (the "D" in SOLID). It would hardcode the agent to Gemini. 

*Why a CLI wrapper for Phase 1?* 
The project requirements specifically asked to use the Gemini CLI initially. It's a great way to prototype without worrying about API keys and network requests in the initial codebase setup.

### B. The Tool Registry (`core/tools/`)
**What we did:**
We created a `BaseTool` interface and a `ToolRegistry`. The registry acts as a library where we register tools (like `FileTool`).

**Why we did it:**
Agents need to be given capabilities dynamically. Instead of hardcoding `if tool == "read_file": do_read()`, we let the agent ask the registry for the tool schema, send that to the LLM, and then dynamically execute whatever tool the LLM requests.

### C. The Agent Layer (`core/agents/`)
**What we did:**
We built the `ResearchAgent`. The most important part of this agent is its **Reasoning Loop** (inside the `execute` method). 

**How it works:**
1. The agent gets a prompt.
2. It sends the prompt + available tools to the LLM.
3. If the LLM replies with a regular text message, the agent returns it.
4. If the LLM replies with a JSON `tool_call` (e.g., "I need to read this file"), the agent pauses, executes the tool via the `ToolRegistry`, appends the result to the prompt, and sends it *back* to the LLM.
5. It repeats this up to 5 times to prevent infinite loops.

**Alternatives:**
*Using LangChain or LlamaIndex.*
*Why we rejected it:* Frameworks like LangChain add a massive layer of complexity and hidden magic. Building our own lightweight reasoning loop gives us 100% control, makes debugging easier, and ensures our code is production-ready. We can adopt specialized frameworks (like LangGraph) later once we actually need their complex routing capabilities.

---

## 3. The Memory Layer: Persistent State (`memory/`)

**What we did:**
We used Django's Object-Relational Mapper (ORM) to create database tables: `UserProfile`, `Conversation`, `Message`, `AgentRun`, and `ToolExecution`.

**Why we did it:**
AI systems need long-term memory. We chose PostgreSQL because it is robust, scalable, and supports JSON fields (which we use in `ToolExecution` to store arbitrary tool inputs/outputs). Django ORM was chosen because it handles migrations automatically and protects against SQL injection.

**Alternatives:**
*Direct SQL or lightweight SQLite.*
*Why we rejected it:* SQLite is great for local prototyping but fails under heavy concurrent web traffic. Writing raw SQL is prone to errors and hard to maintain. Django ORM + PostgreSQL gives us enterprise-grade reliability out of the box.

---

## 4. The Orchestrator Layer (`orchestrator/`)

**What we did:**
We created `SingleAgentOrchestrator`. 

**How it works:**
When a user sends a message, the orchestrator:
1. Fetches or creates the `Conversation` in the database.
2. Saves the User's `Message`.
3. Creates an `AgentRun` record to track performance.
4. Calls `ResearchAgent.execute()`.
5. Saves the Assistant's `Message`.
6. Marks the `AgentRun` as completed.

**Why we did it:**
Agents should not talk to the database. The API should not talk to the Agents. The Orchestrator is the manager. This is the **Mediator Pattern**. It keeps our API views incredibly clean and our agents completely ignorant of the database.

---

## 5. The API Layer (`api/`)

**What we did:**
We used Django REST Framework (DRF) to create a `ChatAPIView` and serializers.

**Why we did it:**
DRF is the industry standard for building APIs in Django. Serializers automatically validate incoming JSON data to ensure it has the correct fields (`message`, `conversation_id`) before it ever touches our Orchestrator. 

**Alternatives:**
*FastAPI or Flask.*
*Why we rejected it:* While FastAPI is faster, Django provides a "batteries-included" ecosystem. The built-in ORM, admin panel, and robust security features allow us to build a full operating system much faster than assembling separate libraries in Flask or FastAPI.

---

## 6. Infrastructure (`docker-compose.yml`)

**What we did:**
We containerized our PostgreSQL database and Redis server using Docker Compose.

**Why we did it:**
"It works on my machine" is the worst phrase in software engineering. By using Docker, we guarantee that every developer working on this project spins up the exact same version of PostgreSQL with the exact same configuration, with a single command (`docker compose up -d`).

---

## Summary

By following these patterns, you have built a system that is:
1. **Extensible:** Want to add an OpenAI provider? Just create `OpenAIProvider(BaseLLMProvider)`.
2. **Testable:** You can test the `ResearchAgent` by passing it a fake `BaseLLMProvider` that returns mocked strings, without ever touching a real database or making network calls.
3. **Robust:** If a tool crashes, the agent catches the exception and feeds it back to the LLM so it can try again.

Welcome to the team! Take some time to read through the `core/agents/research_agent.py` and `orchestrator/single_agent.py` to see these concepts in action.
