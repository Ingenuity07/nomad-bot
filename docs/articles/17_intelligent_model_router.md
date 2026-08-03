# Nomad Bot Intelligent Model Router

We have built a production-grade Intelligent Model Router that runs once at the beginning of a conversation thread. It classifies user requests by complexity (prompt length, keywords, tool needs), matches requirements against a capability registry, handles provider fallbacks, tracks provider health, and locks the chosen model for the rest of the conversation.

---

## 1. Complexity Scorer Heuristics

Rather than making an expensive and slow LLM call to classify prompts, the router calculates a complexity score deterministically:
*   **Prompt Length:** `+3` if length > 1000 characters, `+6` if > 3000 characters, `+10` if > 5000 characters.
*   **Resume/CV Detection:** `+5` if "resume" or "cv" or "job description" is present.
*   **PDF/Document Detection:** `+3` if "pdf" or "document" is present.
*   **Headless Browser commands:** `+2` if "browser", "navigate", or "scrape" is present.
*   **JSON/Structure outputs:** `+1` if "json" or "format" is requested.
*   **Reflection/Critic:** `+4` if "reflect", "critic", or "validate" is present.
*   **Planning/Roadmap:** `+6` if "plan" or "roadmap" is present.

### Score Map to Complexity Tiers:
*   `0 - 5`: **Simple** -> Routes to `qwen3-8b` (Groq / local Ollama)
*   `6 - 12`: **Medium** -> Routes to `qwen3-14b` (Groq / OpenRouter)
*   `13+`: **Critical** -> Routes to `gemini-flash` (Google AI Studio)

---

## 2. Capability & Provider Registry

Every model advertises its strengths and capacity:
```json
{
  "gemini-flash": {
    "name": "gemini-2.5-flash",
    "planning": 10,
    "vision": true,
    "tools": 10,
    "context_limit": 1048576
  }
}
```

---

## 3. Fallback Waterfall Tiers

If a primary provider is unavailable or rate-limited (429 status code), the router failover waterfall automatically engages to try the next healthy provider in order:

*   **Simple waterfall:** Groq -> local Ollama -> Cerebras -> OpenRouter -> Gemini Flash
*   **Medium waterfall:** Groq -> OpenRouter -> Gemini Flash -> local Ollama -> Cerebras
*   **Critical waterfall:** Gemini Flash -> Groq -> OpenRouter -> Cerebras -> local Ollama

---

## 4. Provider Health Cooldown Monitor

We implement a singleton `ProviderHealthMonitor` tracking rate limits (429s), connection timeouts, and server errors (500s) dynamically.
*   If a provider fails, it is blacklisted and marked as unhealthy with a cooldown timer (e.g. 2 minutes).
*   During this period, all routing attempts bypass this provider, avoiding repeated errors and latency spikes.

---

## 5. Thread-safe Conversation Locking

*   **No Switching Mid-Chat:** The router runs once on the first message, selects the optimal model, and persists `selected_model` and `selected_provider` directly in the PostgreSQL `Conversation` record.
*   **Thread Safety:** The active conversation context is bound to a thread-local store on the `IntelligentRouter` instance, protecting simultaneous executions.
*   **Failover Locking:** If a locked provider fails mid-chat, the failover waterfall runs, finds a healthy fallback, and updates the database lock to keep future requests consistent.
