import logging
import os
import threading
from typing import Dict, Any, List

from core.llm_providers.base import BaseLLMProvider
from core.llm.scoring import calculate_complexity_score, get_complexity_tier
from core.llm.health import ProviderHealthMonitor
from core.llm.adapters.gemini import GeminiAdapter
from core.llm.adapters.groq import GroqAdapter
from core.llm.adapters.cerebras import CerebrasAdapter
from core.llm.adapters.openrouter import OpenRouterAdapter
from core.llm.adapters.ollama import OllamaAdapter

logger = logging.getLogger(__name__)

class IntelligentRouter(BaseLLMProvider):
    """
    Intelligent Model Router that classifies incoming requests,
    selects the optimal tier, automatically handles fallbacks,
    tracks provider health, and locks models per conversation.
    """
    
    @staticmethod
    def _parse_fallback_env(env_var_name: str, default_list: list = None) -> list:
        raw = os.environ.get(env_var_name, "").strip()
        if not raw:
            return default_list
        providers = [p.strip() for p in raw.split(",") if p.strip()]
        return providers if providers else default_list

    def __init__(self):
        self._thread_local = threading.local()
        self.health_monitor = ProviderHealthMonitor()
        
        # Instantiate provider adapters with model overrides from os.environ
        self.adapters = {
            "gemini-flash": GeminiAdapter(model_name=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")),
            "groq": GroqAdapter(model_name=os.environ.get("GROQ_MODEL", "mixtral-8x7b-32768")),
            "cerebras": CerebrasAdapter(model_name=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b")),
            "openrouter": OpenRouterAdapter(model_name=os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3-8b-instruct:free")),
            "ollama": OllamaAdapter(model_name=os.environ.get("OLLAMA_MODEL", "qwen3:8b"))
        }
        
        # Parse fallback order from environment variables if defined
        global_priority = self._parse_fallback_env("ROUTER_PROVIDER_PRIORITY", None)
        
        default_simple = ["groq", "ollama", "cerebras", "openrouter", "gemini-flash"]
        default_medium = ["groq", "openrouter", "gemini-flash", "ollama", "cerebras"]
        default_critical = ["gemini-flash", "groq", "openrouter", "cerebras", "ollama"]

        if global_priority:
            default_simple = list(global_priority)
            default_medium = list(global_priority)
            default_critical = list(global_priority)

        self.fallbacks = {
            "simple": self._parse_fallback_env("ROUTER_FALLBACK_SIMPLE", default_simple),
            "medium": self._parse_fallback_env("ROUTER_FALLBACK_MEDIUM", default_medium),
            "critical": self._parse_fallback_env("ROUTER_FALLBACK_CRITICAL", default_critical)
        }

    def set_active_conversation(self, conversation_id: str):
        """Set the active conversation ID on thread-local storage."""
        self._thread_local.conversation_id = conversation_id

    def get_active_conversation(self) -> str:
        """Get the active conversation ID from thread-local storage."""
        return getattr(self._thread_local, "conversation_id", None)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None) -> Dict[str, Any]:
        conversation_id = self.get_active_conversation()
        
        # Try to resolve locked model/provider from DB
        conv = None
        if conversation_id:
            try:
                from memory.models import Conversation
                conv = Conversation.objects.filter(id=conversation_id).first()
            except Exception as e:
                logger.error(f"Error resolving conversation from DB: {e}")

        # Case 1: Model is already locked for this conversation
        if conv and conv.selected_provider and conv.selected_model:
            locked_provider = conv.selected_provider
            logger.info(f"Using locked provider '{locked_provider}' for conversation {conversation_id}")
            
            # Check if locked provider is healthy. If not, trigger failover starting from this provider
            if self.health_monitor.is_healthy(locked_provider):
                adapter = self.adapters.get(locked_provider)
                if adapter:
                    result = adapter.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
                    if result.get("type") != "error":
                        self.health_monitor.report_success(locked_provider)
                        result["provider"] = locked_provider
                        result["model"] = getattr(adapter, "model_name", locked_provider)
                        return result
                    else:
                        logger.warning(f"Locked provider '{locked_provider}' failed: {result.get('text')}")
                        status_code = result.get("status_code", 500)
                        self.health_monitor.report_failure(locked_provider, status_code=status_code)
            
            # Locked provider failed or is unhealthy -> trigger waterfall starting from its index
            logger.info(f"Triggering failover waterfall for conversation {conversation_id}")
            score = calculate_complexity_score(prompt, tools)
            tier = get_complexity_tier(score)
            fallback_list = self.fallbacks.get(tier, self.fallbacks["critical"])
            
            # Find the position of the failed provider to continue from
            start_index = 0
            if locked_provider in fallback_list:
                start_index = fallback_list.index(locked_provider) + 1
            
            remaining_fallbacks = fallback_list[start_index:] + [p for p in fallback_list if p not in fallback_list[start_index:] and p != locked_provider]
            
            for provider in remaining_fallbacks:
                if not self.health_monitor.is_healthy(provider):
                    continue
                adapter = self.adapters.get(provider)
                if not adapter:
                    continue
                
                logger.info(f"Attempting fallback to provider '{provider}' for conversation {conversation_id}")
                result = adapter.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
                if result.get("type") != "error":
                    self.health_monitor.report_success(provider)
                    result["provider"] = provider
                    result["model"] = getattr(adapter, "model_name", provider)
                    # Update database with new locked provider
                    try:
                        conv.selected_provider = provider
                        conv.selected_model = adapter.model_name if hasattr(adapter, "model_name") else provider
                        conv.save()
                        logger.info(f"Updated locked provider to '{provider}' for conversation {conversation_id}")
                    except Exception as db_err:
                        logger.error(f"Error locking fallback provider in DB: {db_err}")
                    return result
                else:
                    status_code = result.get("status_code", 500)
                    self.health_monitor.report_failure(provider, status_code=status_code)
            
            return {"type": "error", "text": "All available model fallback options failed."}

        # Case 2: New request (no locked model yet) or running in CLI/tests
        score = calculate_complexity_score(prompt, tools)
        tier = get_complexity_tier(score)
        fallback_list = self.fallbacks.get(tier, self.fallbacks["critical"])
        
        logger.info(f"Request complexity score: {score} ({tier} tier). Route list: {fallback_list}")
        
        last_error = "No healthy providers available."
        for provider in fallback_list:
            if not self.health_monitor.is_healthy(provider):
                logger.info(f"Provider '{provider}' is currently blacklisted, skipping.")
                continue
                
            adapter = self.adapters.get(provider)
            if not adapter:
                continue
                
            logger.info(f"Routing request to provider '{provider}'")
            result = adapter.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
            
            if result.get("type") != "error":
                self.health_monitor.report_success(provider)
                result["provider"] = provider
                result["model"] = getattr(adapter, "model_name", provider)
                # If conversation exists, lock it
                if conv:
                    try:
                        conv.selected_provider = provider
                        conv.selected_model = adapter.model_name if hasattr(adapter, "model_name") else provider
                        conv.save()
                        logger.info(f"Locked provider '{provider}' for new conversation {conversation_id}")
                    except Exception as db_err:
                        logger.error(f"Error locking provider in DB: {db_err}")
                return result
            else:
                last_error = result.get("text", "Unknown provider error.")
                status_code = result.get("status_code", 500)
                logger.warning(f"Provider '{provider}' failed with code {status_code}: {last_error}")
                self.health_monitor.report_failure(provider, status_code=status_code)
                
        return {"type": "error", "text": f"Router failed to generate response. Last error: {last_error}"}

    def get_providers_status(self) -> Dict[str, Any]:
        """Return full provider health, model names, and priority waterlines."""
        provider_details = []
        for key, adapter in self.adapters.items():
            model_name = getattr(adapter, "model_name", "unknown")
            is_healthy = self.health_monitor.is_healthy(key)
            
            has_key = True
            if key == "gemini-flash":
                has_key = bool(os.environ.get("GEMINI_API_KEY"))
            elif key == "groq":
                has_key = bool(os.environ.get("GROQ_API_KEY"))
            elif key == "cerebras":
                has_key = bool(os.environ.get("CEREBRAS_API_KEY"))
            elif key == "openrouter":
                has_key = bool(os.environ.get("OPENROUTER_API_KEY"))
            elif key == "ollama":
                has_key = True
                
            status_label = "healthy" if (has_key and is_healthy) else ("missing_key" if not has_key else "cooldown")
            provider_details.append({
                "key": key,
                "model_name": model_name,
                "has_key": has_key,
                "is_healthy": is_healthy,
                "status": status_label
            })
            
        return {
            "providers": provider_details,
            "fallbacks": self.fallbacks
        }
