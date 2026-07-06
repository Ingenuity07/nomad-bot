# Article 2: GitHub Integration

## 1. What We Did
We implemented a set of GitHub tools inside [github_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/github_tool.py) and registered them in [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py):
* `github_search_code`: Search code in repositories.
* `github_read_file`: Retrieve text content of files with line numbers.
* `github_write_file`: Commit/update files to a branch (handles creating the branch automatically if it doesn't exist).
* `github_create_pr`: Create Pull Requests.
* Set up a `.env` file at the root to store the personal GitHub Access Token (PAT) and username securely.
* Added Django unit tests inside [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py) to mock `gh` CLI command execution and assert expected results.

---

## 2. Why We Did It
To allow the AIOS bot to autonomously customize the user's resume, inspect job requirements/code repos, and submit pull requests, we need direct code repository integration.
By implementing this step first, the agent gains the ability to:
1. Search and locate resume templates/repositories.
2. Read the resume structure.
3. Commit modified resume versions directly to a feature branch.
4. Raise a PR for user review.

---

## 3. How We Did It
1. **Tool Execution:** Rather than building a complex REST wrapper using third-party packages, we run commands via the official `gh` CLI using Python's `subprocess.run()`.
2. **Credential Management:** We read `GITHUB_TOKEN` from the `.env` file at runtime and pass it in the subprocess execution context as the environment variable `GH_TOKEN` / `GITHUB_TOKEN`.
3. **Branch Creation Lifecycle:** In `github_write_file`, we first check if the branch exists. If not, we query the base branch SHA and create the branch ref dynamically before performing the write/commit.
4. **Safety & Tracing:** File reads automatically attach line numbers to the return string, which helps the ReAct reasoning loop refer to precise lines.

---

## 4. Challenges & Available Options

### Challenge 1: Separating Personal and Work Accounts
The host machine's active `gh` CLI credentials belong to `Shivam-ridecell` (work). The user requested to use their personal GitHub account (`Ingenuity07`).
* **Option A: Run `gh auth login` globally:** Switch active CLI session to personal.
  * *Pros:* Simple.
  * *Cons:* Disrupts the developer's local environment by overriding their work credentials globally.
* **Option B: Pass token explicitly in subprocess environment:**
  * *Pros:* Setting the `GH_TOKEN` environment variable in the subprocess environment overrides the CLI's keyring authentication for that specific command only. It leaves the developer's work credentials intact globally.
  * *Cons:* Requires reading and managing credentials inside our code.
* **Decision:** We chose **Option B**. By reading from `.env` and executing `subprocess.run(..., env=env)`, the agent acts strictly as `Ingenuity07` without altering the host's global CLI login.

### Challenge 2: API Library Choice
* **Option A: PyGithub (or PyGithub/GitPython):**
  * *Pros:* Native Python objects.
  * *Cons:* Requires installing more third-party dependencies, learning custom API patterns, and manually handling rate limit retry/pagination.
* **Option B: Subprocess call to official `gh` CLI:**
  * *Pros:* `gh` CLI handles pagination, auto-formatting, token authentication overrides, and rate-limiting natively. It reduces our code complexity and is highly robust.
  * *Cons:* Requires `gh` CLI installed on the host (which is already present).
* **Decision:** We chose **Option B** to keep the codebase clean, lean, and aligned with RC Guru's architecture.

---

## 5. Technical Details & Future Setup
* Files created/modified:
  * [github_tool.py](file:///Users/shivamsingh/personal/nomad-bot/core/tools/implementations/github_tool.py)
  * [single_agent.py](file:///Users/shivamsingh/personal/nomad-bot/orchestrator/single_agent.py)
  * [tests.py](file:///Users/shivamsingh/personal/nomad-bot/api/tests.py)
  * `.env`
* The next step is **Step 3: Playwright Browser Integration** to give the bot web browsing capabilities.
