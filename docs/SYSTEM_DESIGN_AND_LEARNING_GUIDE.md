# AIOS System Design & Comprehensive Learning Guide

This document is designed to serve as a complete learning resource. It uses the Personal AI Operating System (AIOS) we built as a practical foundation to teach High-Level Design (HLD), Low-Level Design (LLD), system architecture, and core software engineering concepts. 

Whether you are a new developer looking to understand production-grade system architecture, or preparing for a System Design Interview, this guide breaks down every technology and pattern we used: **What it is**, **Why we chose it**, **Alternatives considered**, and **How it works**.

---

## Table of Contents
1. [High-Level Design (HLD) & Architectural Choices](#1-high-level-design-hld--architectural-choices)
2. [Deep Dive into Technologies](#2-deep-dive-into-technologies)
   - [Django & DRF](#django--django-rest-framework-drf)
   - [PostgreSQL](#postgresql-relational-database)
   - [Redis](#redis-in-memory-datastore)
   - [Docker](#docker--containerization)
3. [Low-Level Design (LLD) & Design Patterns](#3-low-level-design-lld--design-patterns)
   - [SOLID Principles in Action](#solid-principles-in-action)
   - [Design Patterns Used](#design-patterns-used)
4. [Domain-Driven Design & Clean Architecture](#4-domain-driven-design--clean-architecture)
5. [Core AI Concepts (ReAct & Tool Calling)](#5-core-ai-concepts)
6. [System Design Interview Cheatsheet](#6-system-design-interview-cheatsheet)

---

## 1. High-Level Design (HLD) & Architectural Choices

### What is HLD?
High-Level Design focuses on the big picture. It defines the major components of a system, how they interact, the technology stack, and how the system scales. 

### Our Architecture: Modular Monolith vs Microservices
We built a **Modular Monolith** using a Layered Architecture.
- **What it is:** The entire application runs as a single deployed unit (one Django server), but the codebase is strictly separated into independent modules (core, api, memory, orchestrator).
- **Why we chose it:** For a new, evolving project, microservices introduce massive overhead (network latency, distributed tracing, complex deployments). A modular monolith gives us the clean separation of microservices, but the deployment simplicity of a monolith.
- **Alternatives:** 
  - *Microservices Architecture:* Splitting the API, Agent execution, and Memory into separate servers.
  - *Serverless (AWS Lambda):* Triggering agent runs via functions. 
- **Interview Concept:** Always start with a Monolith unless you have a strict scaling requirement that demands Microservices. The boundary lines drawn in our `core/` and `api/` folders make it easy to split into microservices *later* if needed.

---

## 2. Deep Dive into Technologies

### Django & Django REST Framework (DRF)
**What it is:** Django is a high-level Python web framework that encourages rapid development and clean, pragmatic design. It uses an MTV (Model-Template-View) pattern. DRF is an extension that allows Django to easily build RESTful APIs.
**Why we chose it:** AI projects are inherently complex. Django provides a "batteries-included" approach: an incredible ORM (Object-Relational Mapper), built-in security (CSRF, XSS protection), and migrations. We don't have to waste time writing boilerplate database connections.
**Alternatives:**
- **FastAPI:** Much faster, native async support, great for ML models. However, you have to piece together your own ORM (SQLAlchemy) and migration system (Alembic). 
- **Flask:** Too minimal. Requires too many third-party plugins to reach Django's baseline.
- **Express.js (Node.js):** Good, but Python is the undisputed king of the AI ecosystem.
**How it works here:** When a user hits `/api/chat/`, the DRF `ChatAPIView` receives the request. A DRF `Serializer` validates the incoming JSON. If valid, it passes the data to our Python classes.

### PostgreSQL (Relational Database)
**What it is:** An open-source, highly extensible, ACID-compliant relational database management system (RDBMS).
**Why we chose it:** 
1. **ACID Properties:** Guarantees data validity (Atomicity, Consistency, Isolation, Durability). We cannot afford to lose user memory or conversation history.
2. **JSONB Support:** AI outputs and tool executions are highly dynamic. PostgreSQL allows us to store arbitrary JSON structures (like tool arguments) inside a relational table (`ToolExecution.input_data`) and actually query against it efficiently.
**Alternatives:**
- **MongoDB (NoSQL):** Great for flexible, document-based data. But we have strict relationships (A User *has many* Conversations, a Conversation *has many* Messages). Relational DBs handle these relationships much better.
- **SQLite:** Great for local testing, but locks the entire database on writes, making it useless for concurrent production use.

### Redis (In-Memory Datastore)
*(Set up in Phase 1 Docker Compose, utilized in future phases)*
**What it is:** An open-source, in-memory key-value data store.
**Why we chose it:** Reading from a hard drive (PostgreSQL) is slow. Reading from RAM (Redis) is blindingly fast. We will use Redis for:
1. **Caching:** Storing frequently accessed data (like User Preferences).
2. **Message Broker:** When we introduce Celery to run AI Agents asynchronously in the background, Redis will act as the queue holding the pending tasks.
**Alternatives:**
- **Memcached:** Good for caching, but lacks persistent storage and data structures (lists, sets) needed for message brokering.
- **RabbitMQ:** An enterprise-grade message broker. Better for highly complex routing, but overkill and harder to manage than Redis for our scale.

### Docker & Containerization
**What it is:** Docker uses OS-level virtualization to deliver software in packages called containers. Containers bundle their own software, libraries, and configuration files.
**Why we chose it:** "It works on my machine" syndrome. Without Docker, setting up PostgreSQL requires downloading it, configuring users, and ensuring port conflicts don't occur. With Docker, `docker-compose up` spins up an identical environment on every developer's machine and in production.
**Alternatives:**
- **Virtual Machines (VMs):** VMs run a full guest Operating System. They are heavy, consume massive RAM, and take minutes to boot. Docker containers share the host OS kernel and boot in milliseconds.

---

## 3. Low-Level Design (LLD) & Design Patterns

### What is LLD?
LLD is the detailed design of individual components. It focuses on classes, interfaces, and methods.

### SOLID Principles in Action
Our `core/` directory is a masterclass in SOLID principles.

1. **S - Single Responsibility Principle:**
   - *Concept:* A class should have one, and only one, reason to change.
   - *Application:* `FileTool` *only* reads files. It doesn't format the output for the LLM. The `GeminiCLIProvider` *only* talks to the CLI. It doesn't route requests to agents.

2. **O - Open-Closed Principle:**
   - *Concept:* Software entities should be open for extension, but closed for modification.
   - *Application:* If we want to add a `WebSearchTool`, we simply create a new class that inherits from `BaseTool`. We **do not** have to modify the `ResearchAgent` code at all. The agent automatically works with any tool registered in the `ToolRegistry`.

3. **L - Liskov Substitution Principle:**
   - *Concept:* Objects of a superclass should be replaceable with objects of its subclasses without breaking the application.
   - *Application:* The Agent expects a `BaseLLMProvider`. Because `GeminiCLIProvider` correctly implements `generate()`, we can pass it to the agent. Later, we can swap it with `OpenAIProvider` without changing a single line of agent code.

4. **I - Interface Segregation Principle:**
   - *Concept:* No client should be forced to depend on methods it does not use.
   - *Application:* Our `BaseTool` has a very strict, minimal interface (`name`, `description`, `parameters`, `execute`). 

5. **D - Dependency Inversion Principle (Crucial for Interviews):**
   - *Concept:* High-level modules (Agents) should not depend on low-level modules (Gemini API). Both should depend on abstractions (Interfaces).
   - *Application:* `ResearchAgent` takes `provider: BaseLLMProvider` in its `__init__`. It depends on the *abstraction*, not the concrete implementation. This is also called **Dependency Injection**.

### Design Patterns Used
- **Strategy Pattern:** Defining a family of algorithms, encapsulating each one, and making them interchangeable. (e.g., our `BaseLLMProvider` implementations).
- **Registry Pattern:** A well-known object that other objects can use to find common objects and services. (e.g., `ToolRegistry`).
- **Facade / Mediator Pattern:** Our `SingleAgentOrchestrator` acts as a facade. The API doesn't need to know how to instantiate agents, fetch history from the database, or handle tool registries. It just calls `orchestrator.handle_request()`.

---

## 4. Domain-Driven Design & Clean Architecture

### The Concept
Clean Architecture (or Onion Architecture) dictates that the core business rules (the Domain) sit at the center of your application. Databases, UI, and external APIs are "details" that sit on the outer edges. Dependencies point **inward**.

### How we applied it:
- **Inner Circle (Entities/Use Cases):** `core/agents`, `core/tools`. This code contains pure Python logic. It has zero `import django` statements. It doesn't know what a database is.
- **Outer Circle (Interface Adapters):** `orchestrator/` translates database models (`Conversation`) into raw data (strings/lists) that the Inner Circle can understand.
- **Outermost Circle (Frameworks):** Django, DRF, PostgreSQL.

*Interview Concept:* If someone asks, "How do you switch from Django to FastAPI?", you can proudly say, "I only rewrite the `api/` folder. My `core/` AI logic and `orchestrator` remain 100% untouched because I used Clean Architecture."

---

## 5. Core AI Concepts (ReAct & Tool Calling)

### The Reasoning Loop (ReAct Framework)
In our `ResearchAgent`, we implement a loop (max 5 iterations). This is based on the **ReAct (Reason + Act)** paper.
**How it works:**
1. **Thought:** The LLM receives the prompt and "thinks" about what it needs.
2. **Action:** It realizes it needs to read a file, so it outputs a JSON object requesting `read_file`.
3. **Observation:** Our Python code halts the LLM, executes the local file read, and appends the file contents back to the prompt.
4. It repeats this process until it has enough context to provide the final **Response**.

**Why it matters:** LLMs are essentially "brains in a jar". They cannot inherently interact with the world. Tool Calling (or Function Calling) bridges this gap. By forcing the LLM to output a specific JSON schema, we turn natural language into deterministic code execution.

---

## 6. System Design Interview Cheatsheet

If you are asked to design an AI system, use these talking points based on this project:

**Q: How do you handle long-running LLM requests timing out the HTTP connection?**
*Answer:* In Phase 1, we used synchronous requests for simplicity. For production, we must implement an asynchronous pattern. The API will accept the request, save it to PostgreSQL, and push a job to Redis. A Celery worker will pick up the job and run the Agent. The API immediately returns a `task_id` (HTTP 202 Accepted). The client can then use WebSockets (Django Channels) to listen for the completed event.

**Q: How do you handle hallucinations where the LLM tries to use a tool that doesn't exist?**
*Answer:* Defensive programming in the Orchestrator/Agent. If `tool_name` is not in the `ToolRegistry`, we catch the `KeyError` and feed an error message *back* to the LLM (e.g., `System: Tool X does not exist. Choose from [A, B]`). The LLM corrects itself in the next iteration.

**Q: Why store `ToolExecutions` in the database?**
*Answer:* Observability and Auditing. If an agent does something destructive or hallucinates, we need a paper trail. Saving the exact `input_data` and `output_data` allows developers to debug the reasoning chain. It is also valuable training data for fine-tuning our own models later.

**Q: Explain Dependency Injection.**
*Answer:* Instead of creating a dependency inside a class (e.g., `self.db = Database()`), we pass it in from the outside (`def __init__(self, db): self.db = db`). This allows us to pass a `MockDatabase` during testing. In our system, the Orchestrator injects the `LLMProvider` and `ToolRegistry` into the `ResearchAgent`.
