# Article 5: Resume & Job Reasoning Agent

## 1. What We Did
We implemented the core reasoning and resume tailoring capability:
* **Specialized Agent Implementation:** Created [job_reasoning_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/job_reasoning_agent.py) implementing the `JobReasoningAgent` subclass. The agent's system prompt dictates a dedicated workflow for browsing job specs, comparing them to the user's resume, outlining gaps, tailoring the resume, committing the customized resume to GitHub, and generating a report.
* **Orchestration Routing:** Modified [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py) to dynamically instantiate and execute either `ResearchAgent` or `JobReasoningAgent` depending on an `agent_type` argument.
* **REST Schema Upgrades:** Updated [serializers.py](file:///Users/shivamsingh/personal/nomad-bot/api/serializers.py) and [views.py](file:///Users/shivamsingh/personal/nomad-bot/api/views.py) to validate and pass the optional `agent_type` field in the POST request body.
* **Unit Tests:** Created unit tests in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) to assert that the orchestrator correctly instantiates the `JobReasoningAgent`, routes requests to it, and logs the correct `agent_type` in the database.

---

## 2. Why We Did It
We need an agent that understands the specific task of job hunting:
1. It knows how to use the `browser_action` tool to navigate job postings and extract page contents.
2. It knows how to fetch the base resume from local files or GitHub (`github_read_file`).
3. It has a high-quality prompt that forces it to compare experiences and customize resumes *honestly* (i.e. highlighting relevant skills without making up credentials).
4. It can commit files back to GitHub to raise PRs.

---

## 3. How We Did It
1. **Inheritance over Duplication:** Rather than rewriting the entire multi-turn ReAct reasoning loop, stuck detection (`LoopState`), context compression, and tool-logging callback handlers, `JobReasoningAgent` inherits directly from `ResearchAgent`. It only overrides `name` and `system_prompt`. This respects DRY (Don't Repeat Yourself) principles.
2. **API Backward Compatibility:** Adding `agent_type` with a default value of `"ResearchAgent"` in the serializer means all existing Phase 1 API requests continue to work without modifications.
3. **Database Audit Integration:** The `agent_type` value is saved directly to the `AgentRun.agent_type` database column, making it simple to filter and audit agent execution logs.

---

## 4. Challenges & Available Options

### Challenge 1: Avoid Code Duplication in Agent Reasoning Loops
Both the general research agent and the job agent require the exact same ReAct execution loop, error boundaries, stuck detection, and callback hooks.
* **Option A: Write two separate agent classes from scratch:**
  * *Pros:* Complete independence.
  * *Cons:* Any improvement or bug fix in the core ReAct loop (like changing iteration counts or improving error strings) must be duplicated across two files, leading to drift.
* **Option B: Subclassing:**
  * *Pros:* All core loop machinery lives in `ResearchAgent`. `JobReasoningAgent` only specifies the personality and instructions (system prompt). Highly maintainable.
  * *Cons:* Tightly couples `JobReasoningAgent` to `ResearchAgent` (acceptable in this context).
* **Decision:** We chose **Option B** (Subclassing) as it is the most elegant OOP pattern for this case.

### Challenge 2: Routing Architecture
* **Option A: Separate API endpoints (e.g. `/api/chat/` and `/api/job-hunting/`):**
  * *Pros:* Clear separation of controllers.
  * *Cons:* Requires updating frontend routes and serializers, and duplicates controller database transactions.
* **Option B: Unified Chat Endpoint with Payload Routing:**
  * *Pros:* Reuses the same WebSocket or REST view. The client simply passes `"agent_type": "JobReasoningAgent"` in the JSON payload, making orchestration a backend-internal decision.
  * *Cons:* The serializer becomes slightly more complex.
* **Decision:** We chose **Option B** (Payload Routing) for cleaner monolith integration.

---

## 5. Technical Details & Future Setup
* Files created/modified:
  * [job_reasoning_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/job_reasoning_agent.py)
  * [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
  * [serializers.py](file:///Users/shivamsingh/personal/nomad-bot/api/serializers.py)
  * [views.py](file:///Users/shivamsingh/personal/nomad-bot/api/views.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
* Next Step: **Step 5: Autonomous Job Application** to expand browser automation to fill out forms and add a human-in-the-loop validation step.
