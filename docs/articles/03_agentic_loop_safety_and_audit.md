# Article 3: Agentic Loop Safety & Audit Upgrades

## 1. What We Did
We upgraded the execution engine of `ResearchAgent` and integrated database auditing in `SingleAgentOrchestrator` to align with the core loop safety patterns in **RC Guru**:
* **Structured Message History:** Modified [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/research_agent.py) to track intermediate turns inside a structured message list, formatting it into a unified prompt string on every iteration.
* **Stuck Loop Detection:** Implemented `LoopState` to track tool calls. If a tool is called 3 times in the last 5 turns with identical discriminator arguments, the agent stops and injects an intervention prompt.
* **Context Compression:** Built `_compress_old_tool_results` to compress tool responses older than 2 iterations (retaining the first 20 and last 10 lines) if they exceed 50 lines.
* **Clean Trace Auditing:** Integrated database logging of tool executions inside [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py) using callback injection, ensuring that `ToolExecution` records are created for every tool invoked.
* **Unit Tests:** Created comprehensive unit tests in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) verifying stuck loop detection, compression limits, and database callback writes.

---

## 2. Why We Did It
1. **Financial and Window Safety:** Long agent loops running dynamic tools (like browser navigations or code search) can grow rapidly. Without stuck loop detection and context compression, loops can run indefinitely, exceeding context limits and generating massive LLM API bills.
2. **Observability:** If an agent fails or produces unexpected results, we need an exact paper trail of which tools were invoked, with what inputs, and the outputs returned.
3. **Clean Architecture:** Django DB imports must not pollute the `core/` agents layer. Injecting a logging callback from the orchestrator keeps the layers decoupled.

---

## 3. How We Did It
1. **LoopState tracking:** We record each tool call inside a `LoopState` instance. It slices the tool arguments to check only high-entropy keys (`repo`, `query`, `path`, etc.) to correctly group conceptually identical calls.
2. **Context Compression Slicing:** When compressing, we split the output by line. We keep the top 20 lines (typically containing headers, counts, or start parameters) and the bottom 10 lines (containing conclusion or errors), omitting the middle block.
3. **Callback Logging:** The agent accept a kwargs parameter `on_tool_execution`. When executing a tool, the agent calls:
   `on_tool_execution(tool_name, tool_args, tool_result, status)`
   The orchestrator implements this callback using standard Django ORM queries on the `ToolExecution` model.

---

## 4. Challenges & Available Options

### Challenge 1: DB Logging Without Violating DDD / Clean Architecture
The Agent Layer (`core/`) must remain pure Python. If we import Django's ORM model `ToolExecution` inside the agent, the layer boundary is violated, making the core code dependent on Django.
* **Option A: Write DB code directly in the agent:**
  * *Pros:* Simple and quick.
  * *Cons:* Breaks the modular structure. If we switch the backend to FastAPI or change database libraries, we must rewrite the core agent code.
* **Option B: Pass an execution callback:**
  * *Pros:* The agent takes `on_tool_execution` as a generic Python Callable. It executes it dynamically without needing to know *what* the callback does (in this case, writing to PostgreSQL via Django ORM). It preserves complete decoupling.
  * *Cons:* Requires passing the callback down the execution chain.
* **Decision:** We chose **Option B** (Callback Injection) as it preserves architectural integrity and SOLID principles.

### Challenge 2: Representing Multi-turn History in Prompt
The LLM Provider interface takes a single `prompt` string. However, stuck detection and context compression require a structured history.
* **Option A: Parse raw prompt string repeatedly:**
  * *Pros:* Keeps prompt as a string.
  * *Cons:* String parsing is error-prone, fragile, and hard to structure.
* **Option B: Manage turns as a list of dictionaries, build prompt at runtime:**
  * *Pros:* Clean list structure `[{"role": "user", "content": ...}]` which makes it simple to run list-based compression and state checks, and then join them into a final string before sending it to the provider.
  * *Cons:* Adds a prompt-formatting step.
* **Decision:** We chose **Option B** for its clean separation of state management and prompt delivery.

---

## 5. Technical Details & Future Setup
* Files modified:
  * [base.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/base.py)
  * [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/research_agent.py)
  * [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
* Next Step: **Step 3: Playwright Browser Integration** to support web interactions.
