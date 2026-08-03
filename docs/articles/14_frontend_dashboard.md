# Article 14: React Frontend Upgrade — Specialized Agents & Pipeline Visualizer

## 1. What We Did
We designed and implemented a major frontend feature set in the React + Vite + TypeScript application:
*   **Dual-Pane Grid System:** Redesigned the main app template into a responsive layout containing the interactive chat workspace on the left and the backend visualizer stream on the right.
*   **Agent Select Dropdown:** Implemented a dropdown to select between:
    *   `Default OS Assistant` (general chat input)
    *   `Job Search Agent` (form fields: job title, location, tech stack keywords, target company)
    *   `Resume Customization Agent` (form fields: raw resume markdown, job description text)
    *   `Headless Browser Agent` (form fields: target navigation URL, execute commands)
*   **Dynamic SVG Node Graph:** Developed an interactive SVG state pipeline visualizer depicting the state machine workflow:
    `Memory Injection` -> `Planner` -> `Executor` -> `Critic` -> `Approval Gate` -> `Form Submit` -> `Memory Extraction`
    The nodes and connecting lines light up dynamically in response to WebSocket events.
*   **WebSocket Event Broker:** Subscribes to the Daphne channel layer for the current conversation ID. It parses node events to animate the active SVG node, update the workflow checklist, and append running tool code logs.
*   **Production Asset Build:** Compiled the entire React/TypeScript asset bundle with zero errors or warnings.

---

## 2. Why We Did It
1.  **Context-Specific Inputs:** Raw chat text blocks are poor interfaces for highly structured tasks. By selecting an agent and filling out parameter forms, the agent receives clear, standard input blocks that align with backend reasoning goals.
2.  **Live Action Diagnostics:** हेडलेस actions (like browser scraping) run in the background. Giving the user an interactive SVG pipeline to see what node is running, which step is active, and what tool outputs are returning increases usability and visibility.
3.  **Human-in-the-Loop Resumption:** Integrating buttons directly inside the visualizer log allows users to click "Approve" or "Reject" to resume graphs instantly, bypassing command line prompts.

---

## 3. Visual Verification

Here is the screenshot of the visual dual-pane layout compiled and loaded in the browser:

![Redesigned React Dashboard Layout](file:///Users/shivamsingh/.gemini/antigravity-ide/brain/26c477f8-7d9f-4ce4-a2eb-e1bc92d5cddd/job_agent_form_1783875627599.png)

---

## 4. Technical Details
*   Files modified:
    *   [App.tsx](file:///Users/shivamsingh/personal/nomad-bot/frontend/src/App.tsx)
    *   [index.css](file:///Users/shivamsingh/personal/nomad-bot/frontend/src/index.css)
