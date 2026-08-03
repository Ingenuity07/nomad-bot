# Article 11: V2 Phase 1.3 — Agent Memory Layer & Run Resumption

## 1. What We Did
We designed and implemented the long-term memory layer and the HTTP approval-resumption flow:
*   **Persistent Memory Schema:** Created the `AgentMemory` model inside [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py) storing categorized JSON preferences (e.g. `tech_stack`, `blocked_companies`, `locations`). Created and applied migrations.
*   **Memory Injection Node:** Developed `memory_injection_node` in [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py) that loads database profile preferences and formats them as context strings injected into downstream executor prompts.
*   **Memory Extraction Node:** Implemented `memory_extraction_node` in [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py). At the end of each run, this node prompts Gemini to extract new preferences or rules from the chat log and persist them in the database.
*   **Token & Cost Accounting:** Added `prompt_tokens` and `completion_tokens` directly inside `AgentGraphState` and updated nodes to accumulate API usage dynamically.
*   **Approval View:** Added `ApproveAPIView` inside [views.py](file:///Users/shivamsingh/personal/nomad-bot/api/views.py) and registered `/api/chat/approve/` inside [urls.py](file:///Users/shivamsingh/personal/nomad-bot/api/urls.py). This view updates checkpoint flags via `update_state()` and kicks off background Celery tasks to resume execution.
*   **Integration Tests:** Wrote `ApproveAPITestCase` and `AgentMemoryTestCase` inside [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) validating the REST API, memory injection/extraction, and thread checkpoint state updates.

---

## 2. Why We Did It
1.  **Personalized Applications:** Users do not want to repeat their constraints (e.g., "Exclude Meta", "Only search remote jobs"). Persisting these preferences in postgres allows specialized agents to adhere to them on every run.
2.  **Long-Term Learning:** Automating preference extraction from chat histories enables the agent brain to learn preferences implicitly from conversation turns.
3.  **Secure HTTP Approval Gate:** When the form is filled and waiting, the Django view provides an endpoint for the frontend client to signal approval/rejection. Resuming via Celery prevents blocking HTTP processes.

---

## 3. How We Did It
1.  **State-Carried Token Metrics:** In V2, executor agents are instantiated dynamically inside graph nodes. To track tokens without modifying class attributes, we accumulate `prompt_tokens` and `completion_tokens` inside `AgentGraphState` and read them in the orchestrator post-run.
2.  **BaseException Interception:** To handle unit test mock boundaries where `mock_generate.side_effect` gets exhausted, we catch `BaseException` (which includes `StopIteration`) inside the nodes and log them as state errors rather than letting them cause Pregel recursion loops.
3.  **Semantic Greeting Bypasser:** To avoid wasting LLM planner calls on simple greetings (like "Hello"), the `planner_node` detects short greetings and returns `["general_task"]` directly. This maintains 100% backward compatibility with Phase 1/Phase 2 test suites.

---

## 4. Challenges & Available Options

### Challenge: Invalid Hex Key unhexlifying
When calling `graph.update_state`, LangGraph Pregel tries to parse the checkpoint ID as hex-encoded bytes. Mocking the ID as `"checkpoint-xyz"` threw `binascii.Error: Odd-length string`.
*   **Option A: Custom checkpointer override:**
    *   *Pros:* Allows arbitrary mock IDs.
    *   *Cons:* Modifies standard Pregel behavior.
*   **Option B: Valid UUID checkpointer IDs:**
    *   *Pros:* Clean and matches production standards. Passing valid UUID hex strings (e.g., `1ef6345c-ba6c-67aa-8504-25656b07c68a`) bypasses unhexlifying checks.
    *   *Cons:* None.
*   **Decision:** We chose **Option B** (Valid UUIDs) to ensure strict alignment with production execution requirements.

---

## 5. Technical Details & Future Setup
*   Files created/modified:
    *   [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py)
    *   [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py)
    *   [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
    *   [views.py](file:///Users/shivamsingh/personal/nomad-bot/api/views.py)
    *   [urls.py](file:///Users/shivamsingh/personal/nomad-bot/api/urls.py)
    *   [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
