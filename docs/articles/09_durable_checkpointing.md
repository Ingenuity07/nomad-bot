# Article 9: V2 Phase 1.1 — Environment & Durable Checkpointing

## 1. What We Did
We set up the infrastructure and libraries to transform Nomad Bot into a durable AI Agent Runtime:
*   **Added Dependencies:** Added `langgraph` and `langchain-core` to the virtual environment and locked them inside `requirements.txt`.
*   **Persistent Checkpoint Schemas:** Declared `AgentCheckpoint` and `AgentCheckpointWrite` models inside [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py) to store serialized thread state checkpoints and pending writes. Created and applied database migrations.
*   **Django Checkpoint Saver:** Designed [checkpoint_saver.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/checkpoint_saver.py) subclassing LangGraph's `BaseCheckpointSaver` to read and write states directly to the database.
*   **ASCII Serialization Wrappers:** Implemented a binary formatting trick (`type.encode("ascii") + b":" + data`) to pack LangGraph's new typed msgpack/json serialization format into single `BinaryField` fields, avoiding multi-column database migrations.
*   **Unit Tests:** Wrote `DjangoCheckpointSaverTestCase` inside [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) validating checkpoint creation (`put`), retrieval (`get_tuple`), updates (`put_writes`), and enumeration (`list`).

---

## 2. Why We Did It
1.  **Durable Execution:** Long-running web scraping or application flows can easily crash due to network drops or server restarts. Persisting states in the database allows the agent to resume execution from the exact failed step instead of starting from scratch.
2.  **Human-in-the-Loop Resumption:** When the agent fills out an application form and halts for human approval, the graph state must be serialized. Storing it in PostgreSQL allows the API to safely resume it hours later when the user sends an approval request.
3.  **Branching & parallel execution:** Multi-agent workflows need to merge parallel channels or handle forks, which requires writing checkpoint logs.

---

## 3. How We Did It
1.  **Serialization Protocol Integration:** Instead of using deprecated `dumps` and `loads` methods, we used the modern `SerializerProtocol` containing `dumps_typed` and `loads_typed`, which return a tuple of `(type_string, data_bytes)`.
2.  **Type Prefixing:** We packed the serializer metadata directly into the database binary block:
    *   *Serialization:* `serialized_bytes = type.encode("ascii") + b":" + data`
    *   *Deserialization:* `type_bytes, data = db_bytes.split(b":", 1)`
3.  **Thread Concurrency Wrapper:** Handled Django's database transaction locks in async contexts by executing database calls inside `sync_to_async` wrappers (using `asgiref.sync.sync_to_async`), guaranteeing thread-safe, non-blocking execution inside async celery tasks.

---

## 4. Challenges & Available Options

### Challenge: JsonPlusSerializer API Changes
The new LangGraph release uses a typed serializer protocol (`dumps_typed`/`loads_typed`) that returns a `(type, bytes)` tuple. Using simple JSON or bytes dumps caused `AttributeError` exceptions.
*   **Option A: Database columns for serializer types:**
    *   *Pros:* Explicit database columns for metadata.
    *   *Cons:* Requires modifying the database schema and adding extra migrations.
*   **Option B: Prefix Type Encoding (ASCII prefix):**
    *   *Pros:* Zero database migration changes, self-contained serializer logic, highly performant.
    *   *Cons:* Slightly custom parsing string separator.
*   **Decision:** We chose **Option B** (Prefix Type Encoding) as it is clean, simple, robust, and isolates the serialization format entirely inside the core code.

---

## 5. Technical Details & Future Setup
*   Files created/modified:
    *   [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py)
    *   [requirements.txt](file:///Users/shivamsingh/personal/nomad-bot/requirements.txt)
    *   [checkpoint_saver.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/checkpoint_saver.py)
    *   [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
