# Environment-Based Router Priority & Model Overrides

We have updated the `IntelligentRouter` in `core/llm/router.py` to allow complete customization of provider fallback orders and model identifiers directly via `.env` variables, eliminating hardcoded provider priorities.

---

## 1. Master Priority Override

You can set a single global priority list that overrides all fallback tiers (`simple`, `medium`, `critical`):

```env
ROUTER_PROVIDER_PRIORITY=ollama,groq,gemini-flash,cerebras,openrouter
```

When `ROUTER_PROVIDER_PRIORITY` is defined, the router will evaluate providers in that exact sequence across all complexity tiers.

---

## 2. Tier-Specific Fallback Overrides

You can also define fine-grained fallback priorities per complexity tier:

```env
# Fast/Free models first for simple prompts
ROUTER_FALLBACK_SIMPLE=groq,ollama,cerebras,openrouter,gemini-flash

# High-throughput models for medium tasks
ROUTER_FALLBACK_MEDIUM=groq,openrouter,gemini-flash,ollama

# High-reasoning models for critical tasks
ROUTER_FALLBACK_CRITICAL=gemini-flash,groq,openrouter
```

If both `ROUTER_PROVIDER_PRIORITY` and a tier-specific variable (e.g., `ROUTER_FALLBACK_SIMPLE`) are set, the tier-specific variable takes precedence for that complexity level.

---

## 3. Model Identifier Overrides

Each provider's model can be configured in `.env` without modifying Python code:

```env
GEMINI_MODEL=gemini-2.5-flash
GROQ_MODEL=mixtral-8x7b-32768
CEREBRAS_MODEL=llama3.1-8b
OPENROUTER_MODEL=meta-llama/llama-3-8b-instruct:free
OLLAMA_MODEL=qwen3:8b
```
