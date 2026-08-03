# Token Usage Metrics & Available Models UI Dashboard

We have implemented per-message token usage tracking (prompt, completion, and total tokens) alongside an **Available Models & Router Status Overlay Modal** in the frontend UI.

---

## 1. Per-Message Token Usage & Provider Badges

*   **REST Adapter Extraction:** Both `OpenAICompatibleAdapter` and `GeminiAPIProvider` parse `usage` metadata from LLM API responses (`prompt_tokens`, `completion_tokens`, `total_tokens`), supplying heuristic token estimations when API usage statistics are omitted.
*   **Database Persistence:** The `Message` model in `memory/models.py` records:
    *   `prompt_tokens`
    *   `completion_tokens`
    *   `total_tokens`
    *   `provider`
    *   `model`
*   **UI Token Footer:** Assistant message bubbles render a subtle token metric badge:
    `⚡ gemini-2.5-flash (gemini-flash) · 370 tokens (120 in / 250 out)`

---

## 2. Available Models & Router Status Drawer Modal

*   **REST Endpoint `/api/providers/`:** `ProviderListAPIView` queries `IntelligentRouter` to report:
    *   List of configured LLM providers (Gemini, Groq, Cerebras, OpenRouter, Ollama).
    *   Configured Model Name (`GEMINI_MODEL`, `GROQ_MODEL`, etc.).
    *   Key Configuration & Health Status (`healthy` green, `missing_key` amber, `cooldown` red).
    *   Active priority fallback sequences across complexity tiers (`simple`, `medium`, `critical`).
*   **Header Control Button:** Users can click **Models & Router** in the chat header to view the status overlay at any time.
