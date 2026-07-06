# Article 7: Celery & Task Scheduling (Asynchronous Headless Mode)

## 1. What We Did
We implemented the asynchronous background processing and cron scheduling architecture:
* **Asynchronous Execution Stack:** Configured Celery, Redis as the broker/backend, and `django-celery-beat` for persistent scheduling.
* **Celery Configuration:** Created [celery.py](file:///Users/shivamsingh/personal/nomad-bot/config/celery.py), updated [__init__.py](file:///Users/shivamsingh/personal/nomad-bot/config/__init__.py), and configured broker settings and backend details in [settings.py](file:///Users/shivamsingh/personal/nomad-bot/config/settings.py).
* **Database Migrations:** Registered the `django_celery_beat` app and successfully applied its database schema migrations.
* **Shared Celery Tasks:** Created [tasks.py](file:///Users/shivamsingh/personal/nomad-bot/memory/tasks.py) implementing `run_agent_task` to execute the selected agent (e.g. `JobReasoningAgent` or `ResearchAgent`) in background worker processes.
* **Controller Integration:** Added the `async_execution` flag to the serializer [serializers.py](file:///Users/shivamsingh/personal/nomad-bot/api/serializers.py). Modified [views.py](file:///Users/shivamsingh/personal/nomad-bot/api/views.py) to immediately return a 202 Accepted status containing the reserved `conversation_id` and the queued Celery `task_id` when the flag is true.
* **Scheduling Engine:** Created [scheduler.py](file:///Users/shivamsingh/personal/nomad-bot/memory/scheduler.py) implementing utility methods to create, list, and disable database-backed interval-based and cron-based agent tasks.
* **Unit Tests:** Added 2 new unit tests in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) to verify the async chat view and ensure scheduling lifecycle operations work against the active PostgreSQL database. All 11 tests passed successfully.

---

## 2. Why We Did It
Headless browser automation and multi-turn LLM reasoning loops can take several minutes to complete. Keeping these runs on a synchronous HTTP thread would block Django worker threads and trigger HTTP request timeouts. Offloading execution to Celery queue workers ensures the API remains fast and responsive. Furthermore, Celery Beat scheduling allows the bot to search for new job openings periodically in the background without user intervention.

---

## 3. How We Did It
1. **Asynchronous Task Dispatches:** When the client posts a message with `async_execution=True`, the view creates the `Conversation` model synchronously, saves the user message to the DB, and dispatches the task using `run_agent_task.delay(...)`.
2. **Database-Backed Schedules:** We use `django-celery-beat`'s database scheduler. Interval and crontab schedules are stored directly in PostgreSQL, allowing administrators or users to schedule cron jobs dynamically at runtime without restarting the Celery daemon processes.
3. **Mock-Based Queue Testing:** Unit tests mock Celery's `delay()` method so that the test suite does not require a running Redis service to pass. The database-backed scheduler models (IntervalSchedule, CrontabSchedule, PeriodicTask) are tested against the test database, ensuring Django ORM integrity.

---

## 4. Challenges & Available Options

### Challenge 1: Celery Broker Choice (Redis vs RabbitMQ)
* **Option A: RabbitMQ:**
  * *Pros:* Native AMQP support, superior message persistence, durable quorum queues.
  * *Cons:* More complex configuration, larger container footprint, higher resource usage on local development systems.
* **Option B: Redis:**
  * *Pros:* Very fast, lightweight, and we already run a Redis container for Django caching/session storage.
  * *Cons:* Less durable than RabbitMQ under heavy crash conditions (not a significant bottleneck for job-hunting agents).
* **Decision:** We chose **Option B** (Redis) as our message broker because of its low resource overhead and simplicity, completely satisfying the requirements of our modular monolith.

### Challenge 2: Client Conversation Synchronization
If the agent runs in the background, how does the client retrieve its output?
* **Option A: Poll the database for messages:** The client polls `/api/chat/?conversation_id=<uuid>` periodically to see if new assistant messages have been created.
* **Option B: WebSocket Events:** Django Channels broadcasts events to the client's socket connection when the task finishes.
* **Decision:** We implemented **Option A** as a baseline for Step 6, since messages and tool executions are automatically logged to PostgreSQL by the database auditing hooks we implemented in Step 2.5. This allows clients to instantly recover the chat history and logs.

---

## 5. Technical Details & Future Setup
* Files created/modified:
  * [celery.py](file:///Users/shivamsingh/personal/nomad-bot/config/celery.py)
  * [__init__.py](file:///Users/shivamsingh/personal/nomad-bot/config/__init__.py)
  * [settings.py](file:///Users/shivamsingh/personal/nomad-bot/config/settings.py)
  * [tasks.py](file:///Users/shivamsingh/personal/nomad-bot/memory/tasks.py)
  * [scheduler.py](file:///Users/shivamsingh/personal/nomad-bot/memory/scheduler.py)
  * [serializers.py](file:///Users/shivamsingh/personal/nomad-bot/api/serializers.py)
  * [views.py](file:///Users/shivamsingh/personal/nomad-bot/api/views.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
  * `requirements.txt` (frozen dependency list)
* Next Phase: Ready to review project architecture and provide the final walkthrough summary.
