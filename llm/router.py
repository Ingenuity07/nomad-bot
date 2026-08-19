import logging
import os
import threading
from typing import Dict, Any, List

from llm.base import BaseLLMProvider
from llm.scoring import calculate_complexity_score, get_complexity_tier
from llm.health import ProviderHealthMonitor
from llm.adapters.gemini import GeminiAdapter
from llm.adapters.groq import GroqAdapter
from llm.adapters.cerebras import CerebrasAdapter
from llm.adapters.openrouter import OpenRouterAdapter
from llm.adapters.ollama import OllamaAdapter

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

    def _generate_with_logging(
        self,
        provider_key: str,
        adapter: Any,
        prompt: str,
        system_prompt: str,
        tools: list,
        prompt_key: str = None,
        prompt_version: int = None,
        template_variables: dict = None,
        prompt_obj: Any = None
    ) -> Dict[str, Any]:
        import time
        import json
        from django.utils import timezone
        from llm.models import PromptRun
        from llm.tracing import get_tracer
        from llm.context import LLMRequestContext
        from opentelemetry import trace

        ctx = LLMRequestContext.get_current() or {}
        correlation_id = ctx.get("correlation_id", "")
        operation = ctx.get("operation", "")
        user_id = ctx.get("user_id", "")
        workspace_id = ctx.get("workspace_id", "")
        context_metadata = ctx.get("metadata", {})

        started_at = timezone.now()
        start_time = time.time()
        result = {"type": "error", "text": "Timeout or failed call"}
        
        logger.info(
            "LLM_REQUEST provider=%s model=%s system_prompt=%r prompt=%r tools=%s",
            provider_key,
            getattr(adapter, "model_name", provider_key),
            system_prompt,
            prompt,
            tools or [],
        )

        tracer = get_tracer()
        span = None
        trace_id = ""
        span_id = ""
        try:
            span = tracer.start_span(operation or f"llm_generate.{provider_key}")
            if span.is_recording():
                span.set_attribute("llm.provider", provider_key)
                span.set_attribute("llm.model", getattr(adapter, "model_name", provider_key))
                span.set_attribute("llm.operation", operation or "llm_generate")
                if prompt_key:
                    span.set_attribute("llm.prompt_key", prompt_key)
                    span.set_attribute("llm.prompt_version", prompt_version)
                span.set_attribute("nomad.correlation_id", correlation_id)
                for k, v in context_metadata.items():
                    span.set_attribute(f"nomad.{k}", str(v))
            span_ctx = span.get_span_context()
            if span_ctx:
                trace_id = format(span_ctx.trace_id, '032x')
                span_id = format(span_ctx.span_id, '016x')
        except Exception as o_err:
            logger.warning(f"Error starting OTel span: {o_err}")

        # Retry transient upstream failures before moving to the next provider.
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        retry_count = 0
        error_type = ""
        error_code = ""
        error_message = ""
        provider_status_code = None

        for attempt in range(3):
            retry_count = attempt
            try:
                result = adapter.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
            except Exception as ad_err:
                result = {
                    "type": "error",
                    "text": f"Adapter exception: {str(ad_err)}",
                    "status_code": 500
                }
            
            if result.get("type") == "error":
                err_text = result.get("text", "")
                status_code = result.get("status_code")
                provider_status_code = status_code
                error_message = err_text
                
                # Categorize error types
                if status_code == 429 or "too many requests" in err_text.lower():
                    error_type = "RATE_LIMIT"
                    error_code = "429"
                elif status_code == 408:
                    error_type = "TIMEOUT"
                    error_code = "408"
                elif status_code in (401, 403):
                    error_type = "AUTHENTICATION"
                    error_code = str(status_code)
                elif status_code and status_code >= 500:
                    error_type = "PROVIDER_ERROR"
                    error_code = str(status_code)
                else:
                    error_type = "UNKNOWN"
                    error_code = "500"

                is_transient = (
                    status_code in retryable_statuses
                    or "too many requests" in err_text.lower()
                    or "service unavailable" in err_text.lower()
                )
                if is_transient and attempt < 2:
                    retry_after = result.get("retry_after")
                    try:
                        wait_sec = max(1, min(int(retry_after), 30)) if retry_after else 2 ** attempt
                    except (TypeError, ValueError):
                        wait_sec = 2 ** attempt
                    logger.warning(
                        f"Provider '{provider_key}' returned transient status {status_code}. "
                        f"Retrying in {wait_sec}s (attempt {attempt + 2}/3)..."
                    )
                    time.sleep(wait_sec)
                    continue
            break

        completed_at = timezone.now()
        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "LLM_RESPONSE provider=%s model=%s latency_ms=%s response=%s",
            provider_key,
            getattr(adapter, "model_name", provider_key),
            latency_ms,
            json.dumps(result, default=str, ensure_ascii=False),
        )
        
        input_tokens = result.get("prompt_tokens") or 0
        output_tokens = result.get("completion_tokens") or 0
        total_tokens = result.get("total_tokens") or (input_tokens + output_tokens)
        model_name = getattr(adapter, "model_name", provider_key)
        
        # Calculate cost
        cost_usd = 0.0
        input_cost = 0.0
        output_cost = 0.0
        m = model_name.lower()
        if "gemini" in m:
            input_cost = (input_tokens / 1_000_000) * 0.075
            output_cost = (output_tokens / 1_000_000) * 0.30
        elif "mixtral" in m:
            input_cost = (input_tokens / 1_000_000) * 0.24
            output_cost = (output_tokens / 1_000_000) * 0.24
        elif "llama3.1-8b" in m or "cerebras" in m:
            input_cost = (input_tokens / 1_000_000) * 0.10
            output_cost = (output_tokens / 1_000_000) * 0.10
        cost_usd = input_cost + output_cost

        # Save to database (durable PromptRun record)
        try:
            response_text = result.get("text", "") if result.get("type") == "text" else json.dumps(result)
            PromptRun.objects.using('telemetry').create(
                purpose="prospecting_run",
                prompt_text=prompt,
                response_text=response_text,
                model_name=model_name,
                temperature=0.0,
                tokens_used=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                # Observability fields
                correlation_id=correlation_id,
                trace_id=trace_id,
                span_id=span_id,
                operation=operation or "llm_generate",
                prompt_version=prompt_version,
                prompt_key=prompt_key or "",
                template_variables=template_variables or {},
                rendered_prompt=prompt,
                provider=provider_key,
                model=model_name,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=cost_usd,
                duration_ms=latency_ms,
                status="success" if result.get("type") != "error" else "error",
                error_type=error_type,
                error_code=error_code,
                error_message=error_message,
                provider_status_code=provider_status_code,
                retry_count=retry_count,
                metadata=context_metadata,
                started_at=started_at,
                completed_at=completed_at
            )
        except Exception as db_err:
            logger.error(f"Failed to save PromptRun log: {db_err}")

        # Finish OTel span
        if span:
            try:
                if result.get("type") == "error":
                    span.set_status(trace.StatusCode.ERROR, error_message)
                    span.record_exception(Exception(error_message))
                else:
                    span.set_status(trace.StatusCode.OK)
                    span.set_attribute("llm.input_tokens", input_tokens)
                    span.set_attribute("llm.output_tokens", output_tokens)
                    span.set_attribute("llm.total_tokens", total_tokens)
                span.end()
            except Exception as o_err:
                logger.warning(f"Error ending OTel span: {o_err}")

        return result

    def set_active_conversation(self, conversation_id: str):
        """Set the active conversation ID on thread-local storage."""
        self._thread_local.conversation_id = conversation_id

    def get_active_conversation(self) -> str:
        """Get the active conversation ID from thread-local storage."""
        return getattr(self._thread_local, "conversation_id", None)

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        tools: list = None,
        prompt_key: str = None,
        system_prompt_key: str = None,
        prompt_version: int = None,
        system_prompt_version: int = None,
        template_variables: dict = None
    ) -> Dict[str, Any]:
        conversation_id = self.get_active_conversation()
        
        prompt_obj = None
        if prompt_key:
            from llm.prompts import PromptRegistry
            try:
                rendered_data = PromptRegistry.render(prompt_key, variables=template_variables, version=prompt_version)
                prompt = rendered_data["rendered_prompt"]
                prompt_version = rendered_data["prompt_version"]
                prompt_key = rendered_data["prompt_key"]
                template_variables = rendered_data["variables"]
                
                # Fetch prompt_obj for metadata tracking
                from llm.models import LLMPrompt
                prompt_obj = LLMPrompt.objects.filter(key=prompt_key, version=prompt_version).first()
            except Exception as e:
                logger.warning(f"Failed to resolve prompt_key '{prompt_key}' from registry: {e}. Falling back to default.")

        if system_prompt_key:
            from llm.prompts import PromptRegistry
            try:
                rendered_sys = PromptRegistry.render(system_prompt_key, variables=template_variables, version=system_prompt_version)
                system_prompt = rendered_sys["rendered_prompt"]
            except Exception as e:
                logger.warning(f"Failed to resolve system_prompt_key '{system_prompt_key}' from registry: {e}. Falling back to default.")

        # Try to resolve locked model/provider from DB
        conv = None
        if conversation_id:
            try:
                from chat.models import Conversation
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
                    result = self._generate_with_logging(
                        locked_provider, adapter, prompt, system_prompt, tools,
                        prompt_key, prompt_version, template_variables, prompt_obj
                    )
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
                result = self._generate_with_logging(
                    provider, adapter, prompt, system_prompt, tools,
                    prompt_key, prompt_version, template_variables, prompt_obj
                )
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
            result = self._generate_with_logging(
                provider, adapter, prompt, system_prompt, tools,
                prompt_key, prompt_version, template_variables, prompt_obj
            )
            
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
