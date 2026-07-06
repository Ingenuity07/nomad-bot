# Article 1: Project Setup, RC Guru Architecture Analysis, and Roadmap

## 1. What We Did
We initialized the documentation structure and established the technical roadmap for the next development cycles of the Personal AI Operating System (AIOS). Specifically:
* Reviewed the [ARCHITECTURE.md](file:///Users/shivamsingh/personal/nomad-bot/ARCHITECTURE.md) of **RC Guru** to extract core architectural patterns.
* Created [BASE_INSTRUCTIONS.md](file:///Users/shivamsingh/personal/nomad-bot/BASE_INSTRUCTIONS.md) in the project root to persist our requirements and roadmap across session compactions.
* Defined the step-by-step phases to build Playwright browser automation, GitHub integration, and resume/job application agents on top of the existing Modular Monolith architecture.

---

## 2. Why We Did It
* **Context Preservation:** LLM context windows can shrink or reset. Having a permanent `BASE_INSTRUCTIONS.md` allows any future LLM session to instantly re-align with the project goals.
* **Learning from Production Patterns:** RC Guru solved major challenges in autonomous agents (loop stuckness, context bloat, token pricing, secure API access). By analyzing it first, we ensure we build our system with these constraints in mind.
* **Traceable Progress:** Establishing a `docs/articles/` folder allows developers to follow the "Why" behind every engineering decision, which is critical for system design review.

---

## 3. How We Did It
1. Analyzed the core layers of the existing modular monolith: `core/` (agents and tool abstraction), `memory/` (DB persistence), `orchestrator/` (routing and logic coordination), and `api/` (rest endpoints).
2. Traced how RC Guru implements its multi-turn loops, context compression (truncating old tool logs), safety filters (blocking protected branches/files), and Celery scheduler.
3. Created a system-level roadmap and checklist.

---

## 4. Challenges & Available Options

### Challenge 1: Preserving Agent Context Across Multi-turn Loops
As agents execute tools, the size of tool outputs (like full web pages or file diffs) grows. 
* **Option A: Simple Accumulation (Current AIOS design):** Keep appending all results to the prompt.
  * *Pros:* Simple.
  * *Cons:* Fast context window exhaustion and high API costs.
* **Option B: Summarization sub-agent:** Call a separate LLM invocation to summarize previous steps.
  * *Pros:* Shorter prompt.
  * *Cons:* Higher latency, extra API call cost, potential loss of precise details (e.g. specific source lines or text to replace).
* **Option C: Context Compression (RC Guru design):** Truncate older tool logs (retaining the first 20 and last 10 lines) after iteration 2.
  * *Pros:* Extremely cheap, preserves formatting start/end details, zero extra LLM calls.
  * *Cons:* Requires deterministic text slicing code.
* **Decision:** We chose **Option C** (Context Compression) as our standard pattern.

### Challenge 2: Browser Automation Framework
To browse job websites and interact with pages, we need a reliable browser engine.
* **Option A: Selenium:**
  * *Pros:* Mature, supports many languages.
  * *Cons:* Verbose API, slower execution, difficult driver setup in containerized environments.
* **Option B: Playwright:**
  * *Pros:* Modern, async-first API, built-in auto-waiting, easy browser binary provisioning (`playwright install`), and excellent screenshotting capabilities.
  * *Cons:* Node.js native, but has excellent Python bindings.
* **Decision:** We chose **Option B** (Playwright) as it is standard, robust, and provides excellent capabilities for AI agents to interact with modern, dynamic Javascript-rendered single-page applications.

---

## 5. Technical Details & Future Setup
* Files created:
  * [BASE_INSTRUCTIONS.md](file:///Users/shivamsingh/personal/nomad-bot/BASE_INSTRUCTIONS.md)
  * [01_project_setup_and_roadmap.md](file:///Users/shivamsingh/personal/nomad-bot/docs/articles/01_project_setup_and_roadmap.md)
* The next step will implement the GitHub integration (`GitHubTool` and configuration), allowing the agent to read repository contexts and push customized code/resumes directly to branches.
