# Article 10: V2 Phase 1.2 — Planner-Executor Workflow Graph

## 1. What We Did
We designed and implemented a Planner-Executor workflow using LangGraph:
*   **Planner Agent:** Created [planner.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/planner.py) containing `PlannerAgent`. It prompts Gemini to parse user requests and return a JSON list of high-level workflow tasks (e.g. `["search_jobs", "tailor_resume", "fill_application", "submit_application"]`).
*   **State Machine Graph:** Created [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py) declaring the `StateGraph` using `AgentGraphState`.
*   **Execution Nodes:**
    *   `planner_node`: Calls `PlannerAgent` to produce the task plan.
    *   `executor_node`: Selects the task at `step_index` and executes a ReAct reasoning turn using either `ResearchAgent` or `JobReasoningAgent` depending on the goal.
    *   `approval_wait_node`: Pauses execution, changes status to `Waiting Approval`, and exits the graph loop.
    *   `submit_node`: Triggers final submission and marks status as `Complete`.
*   **Dynamic State Machine transitions:** Added conditional edges (`route_after_executor`) that inspect `step_index` and `human_approved` status to steer execution.
*   **Unit Tests:** Wrote `V2AgentGraphTestCase` in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) verifying plan generation, sequential execution, approval halting, and thread checkpoint resumption.

---

## 2. Why We Did It
1.  **Lower Token Usage:** Decomposing complex requests into high-level plan steps prevents sending large browser-scraping outputs into general reasoning prompts, optimizing token consumption.
2.  **Clear State Tracking:** Mapping steps to discrete nodes satisfies the V2 roadmap's state machine requirements (`Searching -> Ranking -> Resume -> Form Fill -> Waiting Approval -> Submit -> Complete`).
3.  **Human-in-the-Loop Gate:** Prevents the agent from submitting forms without explicit review by halting state transitions at the `submit_application` plan boundary until `human_approved` is cleared.

---

## 3. How We Did It
1.  **State Schema:** Declared `AgentGraphState` using `TypedDict` containing messages, plan steps, step counters, and status metrics.
2.  **Action Delegation:** Configured `executor_node` to yield execution control back to the graph once a goal completes. If `current_goal` is `submit_application`, the executor returns empty updates, allowing the router to cleanly transition control to the dedicated `submit_node`.
3.  **Resumption from Checkpoints:** Compiled the graph with our custom `DjangoCheckpointSaver`. When a user approves, the state is updated and `graph.invoke` is called using the same `thread_id` config, which restores state and executes the remaining steps.

---

## 4. Challenges & Available Options

### Challenge: Submission Step Execution Duplication
Initially, `submit_application` was processed as a standard plan step inside `executor_node`, setting the status to `"Submit"` and ending without running the dedicated `submit_node` (which updates the status to `"Complete"`).
*   **Option A: Execute submission directly in the executor:**
    *   *Pros:* Fewer nodes in the graph.
    *   *Cons:* Bypasses the specialized submission logic and fails to set status to `"Complete"`.
*   **Option B: Executor Delegation to Submit Node:**
    *   *Pros:* Keeps execution modular. If `executor_node` encounters `submit_application`, it returns empty updates, letting the router transition control to `submit_node`.
    *   *Cons:* Requires one extra routing check.
*   **Decision:** We chose **Option B** (Executor Delegation) as it cleanly decouples tool execution from state machine completion.

---

## 5. Technical Details & Future Setup
*   Files created/modified:
    *   [planner.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/planner.py)
    *   [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py)
    *   [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
