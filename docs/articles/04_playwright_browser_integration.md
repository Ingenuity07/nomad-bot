# Article 4: Playwright Browser Integration

## 1. What We Did
We set up a robust web browsing and form-interaction infrastructure inside the AIOS project:
* **Dependency & Binaries Provisioning:** Installed `playwright` in the Python virtual environment and successfully fetched its browser binaries (Chromium, Firefox, WebKit, FFmpeg) using the `playwright install` tool.
* **Browser Tool Implementation:** Created [browser_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/browser_tool.py) which implements a stateful and context-isolated `BrowserTool` using Playwright's sync API. The tool supports:
  * `navigate`: Connects to any HTTP/HTTPS URL and waits for network idling.
  * `get_content`: Scrapes page text and extracts a structured catalog of interactive components (inputs, buttons, select drop-downs, anchors).
  * `click` / `fill` / `upload_file`: Directly interacts with page elements and submits forms (e.g. attaching resumes).
  * `screenshot`: Captures a screenshot of the browser page and outputs it into the active app data artifacts folder for debugging and visual analysis.
* **Orchestrator Registration:** Registered `BrowserTool` in [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py).
* **Resource Cleanup:** Wrapped the reasoning loop inside [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/research_agent.py) in a `try...finally` block that closes the browser and playwright sessions on completion, avoiding zombie browser processes.
* **Unit Tests:** Added mock assertions in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) verifying URL navigations, text extractions, and interactive element discoveries.

---

## 2. Why We Did It
To apply for jobs autonomously, the bot must be able to:
1. Load job boards (like LinkedIn, Indeed, Greenhouse, or Lever).
2. Extract the job requirements and forms.
3. Type text (like name, email, cover letter).
4. Upload files (customized resumes).
5. Submit the application.
Playwright provides the most modern, async-capable, and reliable browser automation API available for dynamic, JavaScript-rendered web pages.

---

## 3. How We Did It
1. **Playwright Lifecycle Manager (`PlaywrightBrowser`):** Created a class that manages the `playwright`, `browser`, `context`, and `page` singletons. This allows multiple tool calls within a single agent reasoning turn to share the exact same page state (e.g. logging into a site in turn 1 and filling a form in turn 2).
2. **Context-Optimized HTML Parsing:** Sending raw HTML pages to the LLM exhausts context limits and degrades attention. To solve this, `get_content` uses Playwright page queries to extract:
   * Clean text content (`body.inner_text()`).
   * A filtered JSON array of visible interactive nodes (inputs, selects, buttons) containing their type, name, ID, and closest label string, which allows the agent to target precise selectors.
3. **Screenshot Storage:** Saved PNG files using a path helper that targets `/Users/shivamsingh/.gemini/antigravity-ide/brain/26c477f8-7d9f-4ce4-a2eb-e1bc92d5cddd/` directly, enabling the system to render screenshots in user artifacts.

---

## 4. Challenges & Available Options

### Challenge 1: Process and Memory Leaks
Opening headless browsers can lead to orphaned OS processes (zombies) if exceptions are raised or if the agent loop exits prematurely, quickly exhausting host RAM.
* **Option A: Manage lifecycle inside the tool execute() method:** Open and close the browser inside every single tool execution.
  * *Pros:* Simple.
  * *Cons:* Destroys page state. Navigating to page A, clicking a link, and typing inside an input would require 3 separate launches, making stateful sessions impossible.
* **Option B: Keep browser open globally (persistent daemon):**
  * *Pros:* High speed.
  * *Cons:* If the python server runs for days, memory bloats, and sessions can get mixed up across different user conversations.
* **Option C: Wrap execution turn in `try...finally`:** Keep the browser open for the duration of a single `ResearchAgent.execute()` call, sharing state across tool runs, and force-close the browser in the `finally` block when the turn completes.
  * *Pros:* Safe, isolates sessions, prevents process leaks.
  * *Cons:* Slightly slower launch overhead on the first turn (negligible).
* **Decision:** We chose **Option C** (turn-level context wrapping) as it perfectly balances state persistence and resource safety.

### Challenge 2: API Paradigm (Sync vs. Async)
Playwright offers both `async_api` and `sync_api`.
* **Option A: Async API (`asyncio`):**
  * *Pros:* Highly concurrent, handles many pages simultaneously.
  * *Cons:* Requires the calling agent loop and the Django backend views to be fully async. Our agent loop and orchestrator are currently synchronous, so async would introduce thread-safety bugs and require rewriting a lot of boilerplate.
* **Option B: Sync API:**
  * *Pros:* Fits perfectly with the synchronous Django ORM transaction lifecycle and the simple `for` loop in `ResearchAgent`.
  * *Cons:* Blocks the active thread (but Django runs views in separate worker threads anyway).
* **Decision:** We chose **Option B** (Sync API) to align with our Modular Monolith's current concurrency model.

---

## 5. Technical Details & Future Setup
* Files created/modified:
  * [browser_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/browser_tool.py)
  * [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
  * [research_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/research_agent.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
  * `requirements.txt` (frozen dependency list)
* Next Step: **Step 4: Resume & Job Reasoning Agent** to build resume comparisons and customization filters.
