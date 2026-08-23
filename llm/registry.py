from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
from llm.enums import LLMCapability, LLMComplexity

@dataclass
class ModelConfig:
    model_name: str
    provider_key: str  # e.g. "google", "groq", "cerebras", "openrouter", "ollama"
    enabled: bool = True
    capabilities: Set[LLMCapability] = field(default_factory=set)
    pools: Set[LLMComplexity] = field(default_factory=set)
    priority: int = 100
    is_emergency: bool = False
    context_limit: int = 128000

# Centralized Model Registry
MODEL_REGISTRY: Dict[str, ModelConfig] = {
    # --- COMPLEX POOL ---
    "gemini-3.7-flash": ModelConfig(
        model_name="gemini-3.7-flash",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.REASONING, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.COMPLEX},
        priority=1,
    ),
    "gemini-3.6-flash": ModelConfig(
        model_name="gemini-3.6-flash",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.REASONING, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.COMPLEX},
        priority=2,
    ),
    "gemini-3.5-flash": ModelConfig(
        model_name="gemini-3.5-flash",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.REASONING, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.COMPLEX, LLMComplexity.STANDARD},
        priority=3,  # priority 3 in Complex, priority 1 in Standard
    ),
    "gemini-3-flash": ModelConfig(
        model_name="gemini-3-flash",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.REASONING, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.COMPLEX, LLMComplexity.STANDARD, LLMComplexity.SIMPLE},
        priority=4,
    ),
    "gemini-2.5-flash": ModelConfig(
        model_name="gemini-2.5-flash",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.REASONING, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.COMPLEX, LLMComplexity.STANDARD, LLMComplexity.SIMPLE},
        priority=5,
    ),

    # --- STANDARD / LITE POOL ---
    "gemini-3.5-flash-lite": ModelConfig(
        model_name="gemini-3.5-flash-lite",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.STANDARD, LLMComplexity.SIMPLE},
        priority=4,
    ),
    "gemini-3.1-flash-lite": ModelConfig(
        model_name="gemini-3.1-flash-lite",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.STANDARD, LLMComplexity.SIMPLE},
        priority=1,  # priority 1 in Simple pool
    ),
    "gemini-2.5-flash-lite": ModelConfig(
        model_name="gemini-2.5-flash-lite",
        provider_key="google",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.SIMPLE},
        priority=3,
    ),

    # --- EMERGENCY / EXPERIMENTAL POOL ---
    "gemma-4-31b": ModelConfig(
        model_name="gemma-4-31b",
        provider_key="google",
        enabled=False,  # Emergency fallback only when explicitly enabled
        capabilities={LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.COMPLEX, LLMComplexity.STANDARD},
        priority=99,
        is_emergency=True,
    ),
    "gemma-4-26b": ModelConfig(
        model_name="gemma-4-26b",
        provider_key="google",
        enabled=False,  # Emergency fallback only when explicitly enabled
        capabilities={LLMCapability.GENERATION, LLMCapability.SUMMARIZATION},
        pools={LLMComplexity.STANDARD, LLMComplexity.SIMPLE},
        priority=100,
        is_emergency=True,
    ),

    # --- PROVIDER FALLBACK ADAPTERS ---
    "mixtral-8x7b-32768": ModelConfig(
        model_name="mixtral-8x7b-32768",
        provider_key="groq",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION},
        pools={LLMComplexity.SIMPLE, LLMComplexity.STANDARD},
        priority=10,
    ),
    "llama3.1-8b": ModelConfig(
        model_name="llama3.1-8b",
        provider_key="cerebras",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION},
        pools={LLMComplexity.SIMPLE},
        priority=11,
    ),
    "meta-llama/llama-3-8b-instruct:free": ModelConfig(
        model_name="meta-llama/llama-3-8b-instruct:free",
        provider_key="openrouter",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION},
        pools={LLMComplexity.SIMPLE},
        priority=12,
    ),
    "qwen3:8b": ModelConfig(
        model_name="qwen3:8b",
        provider_key="ollama",
        enabled=True,
        capabilities={LLMCapability.STRUCTURED_OUTPUT, LLMCapability.EXTRACTION, LLMCapability.CLASSIFICATION, LLMCapability.GENERATION},
        pools={LLMComplexity.SIMPLE},
        priority=13,
    ),
}

# Pre-defined Model Pools per Complexity Level
MODEL_POOLS: Dict[LLMComplexity, List[str]] = {
    LLMComplexity.COMPLEX: [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash",
        "gemini-2.5-flash",
    ],
    LLMComplexity.STANDARD: [
        "gemini-3.5-flash",
        "gemini-3-flash",
        "gemini-2.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    ],
    LLMComplexity.SIMPLE: [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3-flash",
        "gemini-2.5-flash",
    ],
}

def get_pool_models(complexity: LLMComplexity) -> List[ModelConfig]:
    """Retrieve enabled ModelConfigs for a given complexity pool in priority order."""
    model_keys = MODEL_POOLS.get(complexity, MODEL_POOLS[LLMComplexity.STANDARD])
    configs = []
    for key in model_keys:
        cfg = MODEL_REGISTRY.get(key)
        if cfg and cfg.enabled and not cfg.is_emergency:
            configs.append(cfg)
    return configs

def get_global_fallback_models(exclude_keys: Set[str] = None) -> List[ModelConfig]:
    """Retrieve remaining enabled non-emergency models across the entire registry."""
    exclude = exclude_keys or set()
    configs = [
        cfg for key, cfg in MODEL_REGISTRY.items()
        if cfg.enabled and not cfg.is_emergency and key not in exclude
    ]
    configs.sort(key=lambda c: c.priority)
    return configs
