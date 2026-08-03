# User Model Selection & Auto-Routing Preservation Architecture

We have introduced explicit user model selection in the chat header while preserving the dynamic complexity-based auto-routing engine.

---

## 1. Dual Mode Selection Architecture

*   **Mode 1: `Auto (Intelligent Router)` (Default):**
    *   Evaluates prompt complexity scoring (Simple 0-5, Medium 6-12, Critical 13+).
    *   Selects provider using priority waterlines (`ROUTER_PROVIDER_PRIORITY` or tier defaults).
    *   Applies automated health monitoring failovers on 429 / connection errors.
*   **Mode 2: Explicit Provider Override:**
    *   Allows users to manually pick a provider (`gemini-flash`, `groq`, `cerebras`, `openrouter`, `ollama`).
    *   Locks `Conversation.selected_provider` and `Conversation.selected_model` directly in PostgreSQL.

---

## 2. API & Data Flow

*   **Chat Request Payload (`POST /api/chat/`):**
    ```json
    {
      "message": "Write a Python script",
      "conversation_id": "30d1505e-7490-4ebb-81a6-49776c3287f5",
      "agent_type": "ResearchAgent",
      "selected_provider": "groq"
    }
    ```
*   **Orchestrator Override Handler (`orchestrator/single_agent.py`):**
    *   `SingleAgentOrchestrator.handle_request()` checks `selected_provider`. If present and not `"auto"`, it locks the requested provider adapter on the conversation before triggering LangGraph nodes.
