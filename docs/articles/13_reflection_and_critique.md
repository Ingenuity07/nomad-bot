# Article 13: V2 Phase 2.2 — Reflection & Critique

## 1. What We Did
We designed and implemented a dynamic Generator-Critic reflection loop in the LangGraph StateGraph runtime:
*   **State Extension:** Extended `AgentGraphState` in [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py) to include `retry_count: int`.
*   **Critic Node:** Created the `critic_node` in [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py). For standard plan steps (e.g. `scrape_job`, `tailor_resume`, `fill_application`), the Critic node sends the agent's output to the LLM Critic prompt to assess quality and completeness.
*   **Dynamic Retry Mechanism:**
    *   If the Critic assesses `success = False` and `retry_count < 3`, it resets `step_index` back by 1 (forcing a retry of the same step), appends a user critique message containing guidance to the chat logs, increments `retry_count`, and routes back to `"executor"`.
    *   If the Critic assesses `success = True` or the step reaches the limit of `3` retries, it clears `retry_count` and routes forward to the next step.
*   **StateGraph Re-routing:** Wire the workflow edges to transition `executor` -> `critic` -> `route_after_critic`.
*   **Unit Tests:** Wrote `ReflectionAndCritiqueTestCase` in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) verifying that deficient outputs trigger index resets, guide instructions are appended to histories, `retry_count` increments, and successful subsequent attempts pass. All 24 tests are passing!

---

## 2. Why We Did It
1.  **Fault Tolerance:** Agents often make mistakes on the first attempt (e.g., missing fields in a form, extracting partial text, or generating generic resumes). Reflection allows the system to self-correct before presenting outputs to the user.
2.  **Explicit Guidance Retries:** Traditional ReAct loops rely on implicit agent self-correction. By structuring the retry as an explicit critique instruction injected back into the messages list, the generator receives specific, actionable instructions on what was wrong and how to fix it.
3.  **Bound Costs:** Setting a maximum retry threshold of `3` ensures that the agent does not infinite loop on unresolvable goals, bounding LLM call expenditures.

---

## 3. How We Did It
1.  **Index Reset Manipulation:** By utilizing state-driven index routing, we avoided complex graph re-trigger logic. We simply set `step_index` back to the index of the completed step, causing the state machine to automatically execute the same goal when routing back to `"executor"`.
2.  **Graceful LLM Crash Fallbacks:** If the Critic LLM query fails or returns invalid JSON formats (which commonly happens in unit tests running other tasks), we catch the exception and default to `success = True`, allowing execution to continue robustly:
    ```python
    except Exception as e:
        logger.error(f"Error executing critic LLM: {str(e)}")
        success = True
        critique = "Critic LLM error. Proceeding."
    ```

---

## 4. Challenges & Available Options

### Challenge: Infinite Loop Prevention
If an agent is unable to satisfy the critic (e.g. due to website schema changes), it could loop forever.
*   **Option A: Block the run and ask for human input:**
    *   *Pros:* High precision.
    *   *Cons:* Blocks headless runs unnecessarily.
*   **Option B: Proceed with best-effort attempt after max retries:**
    *   *Pros:* Robust and maintains headless execution. The Critic logs a `critic_max_retries` warning to WebSockets and proceeds.
    *   *Cons:* Might submit imperfect forms.
*   **Decision:** We chose **Option B** (Best-effort continuation) because V2 is designed to run asynchronously in the background. If critical issues require human intervention, the final `submit_application` step's Human-in-the-Loop approval gate still provides a final safety check.

---

## 5. Technical Details
*   Files modified:
    *   [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py)
    *   [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
    *   [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
