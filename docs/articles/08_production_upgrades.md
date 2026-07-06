# Article 8: Production-Grade Security, Locking, Cost Auditing, and Vision Upgrades

## 1. What We Did
We implemented several critical production features to match RC Guru's capabilities:
* **Distributed Task Locking:** Integrated Redis-backed Django cache locking in [tasks.py](file:///Users/shivamsingh/personal/nomad-bot/memory/tasks.py). Every Celery task acquisition sets a unique key (`lock:run_agent_task:{username}:{conversation}`) using an atomic `cache.add()` operation, preventing concurrent executions.
* **GitHub Write Protection:** Modified [github_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/github_tool.py) to block direct writes to protected branches (`main`, `master`, `production`) and restrict commits targeting sensitive files (CI/CD workflows under `.github/workflows/*`, secrets, `.env*` files, docker configurations, or private keys).
* **Browser Domain Safety:** Updated [browser_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/browser_tool.py) to block navigations targeting cloud metadata IPs (`169.254.169.254`), host loopbacks (`127.0.0.1`, `localhost`, `::1`), or host file paths (`file://`).
* **Token & Cost Auditing:** Expanded the `AgentRun` table in [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py) with columns for `prompt_tokens`, `completion_tokens`, and `total_cost`.
* **API Cost Injection:** Configured [gemini_api.py](file:///Users/shivamsingh/personal/nomad-bot/core/llm_providers/gemini_api.py) and [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/research_agent.py) to parse `usageMetadata` and accumulate token usage across reasoning turns. Updated [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py) to calculate and persist USD costs based on Gemini 2.5 Flash rates.
* **Vision Capabilities:** Created [vision_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/vision_tool.py) introducing `AnalyzeScreenshotTool` which encodes screenshot files from artifacts into base64 and executes multimodal Gemini REST queries.
* **Unit Tests:** Added 5 new tests in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) validating locking, safety blocks, cost calculations, and vision analysis.

---

## 2. Why We Did It
1. **Concurreny Safety:** If Celery Beat triggers a task while a previous run is still active, multiple headless browsers will launch on the host, causing race conditions and server memory crashes.
2. **Malicious Prompt Injection Protection:** Headless LLM agents can be manipulated by malicious text parsed on web pages. If an agent visits a page containing `"write a key to secrets/private.key"`, safety guards are required at the tool level to block execution.
3. **Host Security:** Restricts the browser from fetching local configuration files or AWS metadata API keys.
4. **Visual Debugging:** The agent needs to verify why forms failed to submit. Instantiating a vision tool allows it to analyze form states dynamically.

---

## 3. How We Did It
1. **Atomic Locking (`cache.add`):** Returns `True` only if the key doesn't exist. Releasing the lock deletes the key inside a `finally` block.
2. **Prioritized Browser Guards:** Refactored `BrowserTool.execute()` to run safety checks on the requested URL *before* invoking Playwright's `get_page()`. This avoids launching headless Chrome processes entirely when blocked domains are requested.
3. **Decoupled Vision Tool:** Created `AnalyzeScreenshotTool` as a standard agent tool. This allows the model to request visual inspection of screenshots on-demand, saving token usage compared to sending raw images in every turn.
4. **Test Environment Isolation:** Bypassed Django's database transaction locks in tests (triggered by Playwright's event loops) by enabling `os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"`.

---

## 4. Challenges & Available Options

### Challenge 1: Distributed Lock Backend (Redis vs Database Cache)
* **Option A: Database cache table:**
  * *Pros:* No extra service requirements.
  * *Cons:* Higher database disk write overhead.
* **Option B: Redis cache backend:**
  * *Pros:* Very fast, atomic `SETNX` operations in memory, matches RC Guru's performance profiles.
  * *Cons:* Requires a running Redis container.
* **Decision:** We chose **Option B** (Redis Cache Backend) since we already run Redis for our Celery message broker, allowing clean and fast distributed locking.

### Challenge 2: Cost Calculation Accuracy
API model prices can change. Hardcoding them in database models can lead to stale cost data.
* **Option A: Query pricing API endpoints dynamically:**
  * *Pros:* Always accurate.
  * *Cons:* Adds network latencies and external points of failure to every agent finish step.
* **Option B: Apply static Decimal configuration:**
  * *Pros:* Fast, simple, reliable.
  * *Cons:* Requires a code deploy if model pricing changes.
* **Decision:** We chose **Option B** (static Decimal configurations in the orchestrator) using Gemini 2.5 Flash rates ($0.075 / 1M input, $0.30 / 1M output) as it is robust and easy to update.

---

## 5. Technical Details & Future Setup
* Files created/modified:
  * [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py)
  * [settings.py](file:///Users/shivamsingh/personal/nomad-bot/config/settings.py)
  * [tasks.py](file:///Users/shivamsingh/personal/nomad-bot/memory/tasks.py)
  * [github_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/github_tool.py)
  * [browser_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/browser_tool.py)
  * [gemini_api.py](file:///Users/shivamsingh/personal/nomad-bot/core/llm_providers/gemini_api.py)
  * [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/research_agent.py)
  * [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
  * [vision_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/vision_tool.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
