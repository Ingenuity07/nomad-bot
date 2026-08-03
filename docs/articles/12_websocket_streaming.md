# Article 12: V2 Phase 2.1 — WebSocket Streaming UI

## 1. What We Did
We designed and implemented a real-time event streaming interface for Nomad Bot:
*   **Django Channels Setup:** Installed `daphne`, `channels`, and `channels-redis`. Configured `ASGI_APPLICATION` and Redis-backed `CHANNEL_LAYERS` in [settings.py](file:///Users/shivamsingh/personal/nomad-bot/config/settings.py).
*   **ASGI Routing:** Updated [asgi.py](file:///Users/shivamsingh/personal/nomad-bot/config/asgi.py) to route HTTP requests through standard Django ASGI and WebSocket requests through Channels `URLRouter` and `AuthMiddlewareStack`.
*   **Consumer & Routing:** Created [routing.py](file:///Users/shivamsingh/personal/nomad-bot/api/routing.py) and [consumers.py](file:///Users/shivamsingh/personal/nomad-bot/api/consumers.py) defining `ChatConsumer` to subscribe and unsubscribe clients to Redis channel groups named `chat_{conversation_id}`.
*   **Graph Streaming Triggers:** Integrated `stream_agent_update` utility within [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py) that broadcasts planner events (`planner_start`, `planner_plan`), executor step boundaries (`executor_step_start`, `executor_step_end`), tool calls, human approval holds, and run completions.
*   **Dual-Pane Dashboard:** Redesigned [chat.html](file:///Users/shivamsingh/personal/nomad-bot/templates/chat.html) into a premium dual-pane UI. The left pane retains chat messaging, while the right pane renders live plan checkpoints, tool executions, and interactive buttons for human approval resumption.
*   **Integration Tests:** Wrote `WebSocketStreamingTestCase` in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) validating the connection handshake, channel layer message deliveries, and `stream_agent_update` event outputs.

---

## 2. Why We Did It
1.  **Thinking Process Visibility:** Job application cycles are long and consist of multiple autonomous browser turns. WebSockets allow the user to watch exactly what the ReAct loop is doing at any microsecond.
2.  **Bandwidth Efficiency:** Real-time push communication is significantly more efficient than HTTP polling, reducing server-side query loads.
3.  **Unified HITL Workspace:** Displaying the form filled status and screenshot along with immediate "Approve" buttons inside the execution log creates a seamless, interactive human-in-the-loop experience.

---

## 3. How We Did It
1.  **Async Loop Context Detection:** In async test environments, `async_to_sync` blocks and causes deadlocks because the test runner already occupies the loop. We resolved this by detecting the active event loop:
    ```python
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    ```
    If an active loop exists, we publish events in the background via `loop.create_task()`, otherwise we delegate to `async_to_sync` for synchronous views and Celery tasks.
2.  **Delayed Router Imports:** To prevent Django's `AppRegistryNotReady` exception, we perform router and consumer imports inside [asgi.py](file:///Users/shivamsingh/personal/nomad-bot/config/asgi.py) *after* executing `django_asgi_app = get_asgi_application()`.

---

## 4. Challenges & Available Options

### Challenge: Deadlocking Async Tests in Channels
Calling `async_to_sync` inside a running async loop throws an error or blocks indefinitely, failing the test suite.
*   **Option A: Split sync and async tests:**
    *   *Pros:* Separates environments.
    *   *Cons:* Tedious and does not address mixed code paths.
*   **Option B: Context-aware executor detection:**
    *   *Pros:* Completely robust. The code automatically picks the correct scheduling API (async task schedule vs sync block wrapper) based on runtime conditions.
    *   *Cons:* Slightly more code lines.
*   **Decision:** We chose **Option B** (Context-aware detection) to keep our central `stream_agent_update` utility fully environment-agnostic.

---

## 5. Technical Details
*   Files created/modified:
    *   [settings.py](file:///Users/shivamsingh/personal/nomad-bot/config/settings.py)
    *   [asgi.py](file:///Users/shivamsingh/personal/nomad-bot/config/asgi.py)
    *   [routing.py](file:///Users/shivamsingh/personal/nomad-bot/api/routing.py)
    *   [consumers.py](file:///Users/shivamsingh/personal/nomad-bot/api/consumers.py)
    *   [v2_graph.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/v2_graph.py)
    *   [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
    *   [chat.html](file:///Users/shivamsingh/personal/nomad-bot/templates/chat.html)
    *   [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
