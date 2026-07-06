# Article 6: Autonomous Job Application

## 1. What We Did
We implemented the profile context storage and form-filling capabilities to support autonomous job applications:
* **UserProfile Schema Upgrade:** Modified [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py) to store professional profile data (`full_name`, `phone`, `linkedin_url`, `github_url`, `portfolio_url`). Generated and applied the database migrations.
* **Profile Context Injection:** Updated [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py) to load these details from the active profile and package them into a `user_profile_data` context dictionary.
* **Agent prompt decoration:** Overwrote the `execute` method in [job_reasoning_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/job_reasoning_agent.py) to append the `user_profile_data` directly to the prompt, ensuring the LLM is aware of the user's name, email, and social links when filling out Greenhouse/Lever forms.
* **Unit Tests:** Added a test in [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) to verify that the profile data is correctly injected into the prompt before the parent execution loop is triggered. All 9 tests passed.

---

## 2. Why We Did It
To fill out job application forms automatically:
1. The agent must have access to the applicant's contact and profile details.
2. The data must be accessible cleanly without tightly coupling the agent code to Django or PostgreSQL.
3. Keeping the details inside the prompt allows the model to map form fields (e.g. "Full Name" -> `full_name`) dynamically using the `browser_action` tool.

---

## 3. How We Did It
1. **Dynamic Schema Migration:** Added standard Django ORM model fields. Migrations were successfully executed via `makemigrations` and `migrate`.
2. **Stateless Profile Loading:** The orchestrator resolves the database model and extracts the fields into a plain Python dictionary. This maintains Clean Architecture as the Agent layer only receives raw string keys and values.
3. **Safety and Human-in-the-Loop:** For submitting applications:
   * **Stage 1 (Prepare):** The agent navigates to the job URL, customizes the resume, uploads it, fills all inputs, captures a screenshot using `browser_action(action='screenshot')`, and asks the user for confirmation.
   * **Stage 2 (Submit):** Once verified, the user gives the final confirmation (e.g. "submit it"), and the agent executes the final click action on the submit selector.

---

## 4. Challenges & Available Options

### Challenge 1: Clean Architecture Boundaries for Profile Data
The Agent layer (`core/`) must not import Django ORM models.
* **Option A: Import UserProfile in the agent:**
  * *Pros:* Simple database query.
  * *Cons:* Breaks the clean Onion architecture, coupling the agent layer directly to Django's ORM.
* **Option B: Pass profile fields dynamically as parameters:**
  * *Pros:* The orchestrator reads the model and injects the fields as a plain dictionary (`user_profile_data`) into the agent's execution parameters. The agent remains a pure, database-agnostic Python object.
  * *Cons:* Requires updating orchestration parameter flows.
* **Decision:** We chose **Option B** (Parameter Injection) to respect architecture layering.

### Challenge 2: Human-in-the-Loop Validation in Synchronous API View
Because HTTP REST calls have standard 30s timeouts, the agent cannot block the HTTP execution thread to wait for a Slack approval click.
* **Option A: Run fully autonomously (no approval):**
  * *Pros:* High speed.
  * *Cons:* Extremely risky. The agent might upload outdated details, fill fields incorrectly, or submit applications to the wrong roles.
* **Option B: Two-stage stateless execution:**
  * *Pros:* Stage 1 performs the filling and returns the screenshot/form report, returning immediately to the user. Stage 2 executes the final click on the submit button only when the user sends a follow-up confirmation request. Safe and perfectly compatible with synchronous HTTP threads.
  * *Cons:* Requires the agent to re-navigate and re-fill the form on Stage 2 (mitigated once we add Celery Beat / task queues in the next phase).
* **Decision:** We chose **Option B** (Two-stage execution) for HTTP security.

---

## 5. Technical Details & Future Setup
* Files created/modified:
  * [models.py](file:///Users/shivamsingh/personal/nomad-bot/memory/models.py)
  * [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
  * [job_reasoning_agent.py](file:///Users/shivamsingh/personal/nomad-bot/core/agents/job_reasoning_agent.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
* Next Step: **Step 6: Celery & Task Scheduling (Asynchronous Headless Mode)** to move these runs into durable background task queues.
