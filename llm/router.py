import logging
import os
import json
import time
import threading
from typing import Dict, Any, List, Optional, Set, Type
from pydantic import BaseModel, ValidationError

from llm.base import BaseLLMProvider
from llm.scoring import calculate_complexity_score, get_complexity_tier
from llm.health import ProviderHealthMonitor
from llm.adapters.gemini import GeminiAdapter
from llm.adapters.groq import GroqAdapter
from llm.adapters.cerebras import CerebrasAdapter
from llm.adapters.openrouter import OpenRouterAdapter
from llm.adapters.ollama import OllamaAdapter

from llm.enums import LLMOperation, LLMComplexity, LLMCapability, LLMErrorCategory
from llm.contracts import LLMRequest, LLMResult
from llm.registry import MODEL_REGISTRY, get_pool_models, get_global_fallback_models, ModelConfig

logger = logging.getLogger(__name__)

RETRYABLE_ERROR_CATEGORIES = {
    LLMErrorCategory.RATE_LIMITED,
    LLMErrorCategory.QUOTA_EXCEEDED,
    LLMErrorCategory.TEMPORARY_PROVIDER_ERROR,
    LLMErrorCategory.MODEL_UNAVAILABLE,
    LLMErrorCategory.TIMEOUT,
    LLMErrorCategory.SCHEMA_VALIDATION_FAILED,
}

def classify_error(status_code: Optional[int] = None, error_text: str = "") -> LLMErrorCategory:
    err_lower = error_text.lower()
    
    # Check for quota limits first (since they might come with 429 status code)
    if "quota" in err_lower or "daily limit" in err_lower or "exhausted" in err_lower:
        return LLMErrorCategory.QUOTA_EXCEEDED

    if status_code == 429:
        return LLMErrorCategory.RATE_LIMITED
    if status_code in (401, 403):
        return LLMErrorCategory.AUTHENTICATION_ERROR
    if status_code == 404:
        return LLMErrorCategory.MODEL_UNAVAILABLE
    if status_code == 400:
        return LLMErrorCategory.INVALID_REQUEST
    if status_code in (500, 502, 503, 504):
        return LLMErrorCategory.TEMPORARY_PROVIDER_ERROR
    
    if "rate limit" in err_lower or "429" in err_lower or "resource_exhausted" in err_lower:
        return LLMErrorCategory.RATE_LIMITED
    if "timeout" in err_lower or "timed out" in err_lower:
        return LLMErrorCategory.TIMEOUT
    if "schema" in err_lower or "validation" in err_lower:
        return LLMErrorCategory.SCHEMA_VALIDATION_FAILED
    if "not found" in err_lower:
        return LLMErrorCategory.MODEL_UNAVAILABLE
    return LLMErrorCategory.UNKNOWN


class IntelligentRouter(BaseLLMProvider):
    """
    Intelligent Generic LLM Router that selects models based on capabilities & complexity,
    manages pool fallbacks & global fallbacks, validates structured output,
    and logs telemetry/tracing metadata.
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
        
        # Adapters cache per model
        self._adapter_cache: Dict[str, Any] = {}
        
        # Legacy adapter dictionary for backward compatibility
        self.adapters = {
            "gemini-flash": GeminiAdapter(model_name=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")),
            "groq": GroqAdapter(model_name=os.environ.get("GROQ_MODEL", "mixtral-8x7b-32768")),
            "cerebras": CerebrasAdapter(model_name=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b")),
            "openrouter": OpenRouterAdapter(model_name=os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3-8b-instruct:free")),
            "ollama": OllamaAdapter(model_name=os.environ.get("OLLAMA_MODEL", "qwen3:8b"))
        }
        
        # Legacy fallback configurations
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

    def _get_adapter_for_model(self, model_config: ModelConfig) -> Any:
        key = model_config.model_name
        if key in self._adapter_cache:
            return self._adapter_cache[key]
        
        provider_type = model_config.provider_key
        if provider_type == "google":
            adapter = GeminiAdapter(model_name=model_config.model_name)
        elif provider_type == "groq":
            adapter = GroqAdapter(model_name=model_config.model_name)
        elif provider_type == "cerebras":
            adapter = CerebrasAdapter(model_name=model_config.model_name)
        elif provider_type == "openrouter":
            adapter = OpenRouterAdapter(model_name=model_config.model_name)
        elif provider_type == "ollama":
            adapter = OllamaAdapter(model_name=model_config.model_name)
        else:
            adapter = GeminiAdapter(model_name=model_config.model_name)
            
        self._adapter_cache[key] = adapter
        return adapter

    def _generate_with_logging(
        self,
        provider_key: str,
        adapter: Any,
        prompt: str,
        system_prompt: str,
        tools: list,
        prompt_key: str = None,
        prompt_version: int = None,
        system_prompt_key: str = None,
        system_prompt_version: int = None,
        template_variables: dict = None,
        prompt_obj: Any = None
    ) -> Dict[str, Any]:
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
        context_metadata = dict(ctx.get("metadata", {}))
        if system_prompt_key:
            context_metadata["system_prompt_key"] = system_prompt_key
        if system_prompt_version:
            context_metadata["system_prompt_version"] = system_prompt_version

        started_at = timezone.now()
        start_time = time.time()
        result = {"type": "error", "text": "Timeout or failed call"}
        
        model_name = getattr(adapter, "model_name", provider_key)
        logger.info(
            "LLM_REQUEST provider=%s model=%s system_prompt=%r prompt=%r tools=%s",
            provider_key,
            model_name,
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
                span.set_attribute("llm.model", model_name)
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
                cat = classify_error(status_code, err_text)
                if cat == LLMErrorCategory.QUOTA_EXCEEDED:
                    is_transient = False
                    
                if is_transient and attempt < 2:
                    retry_after = result.get("retry_after")
                    wait_time = int(retry_after) if (retry_after and retry_after.isdigit()) else (attempt + 1)
                    logger.warning(
                        f"Provider '{provider_key}' returned transient status {status_code or 'error'}. "
                        f"Retrying in {wait_time}s (attempt {attempt + 1}/3)..."
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    break
            else:
                break

        latency_ms = int((time.time() - start_time) * 1000)

        input_tokens = result.get("prompt_tokens") or max(1, len(prompt + system_prompt) // 4)
        output_tokens = result.get("completion_tokens") or max(1, len(str(result.get("text", ""))) // 4)
        total_tokens = result.get("total_tokens") or (input_tokens + output_tokens)

        # Cost estimation per 1k tokens
        cost_map = {
            "gemini-3.7-flash": (0.0001, 0.0004),
            "gemini-3.6-flash": (0.0001, 0.0004),
            "gemini-3.5-flash": (0.0001, 0.0004),
            "gemini-3-flash": (0.0001, 0.0004),
            "gemini-2.5-flash": (0.0001, 0.0004),
            "gemini-3.5-flash-lite": (0.00005, 0.0002),
            "gemini-3.1-flash-lite": (0.00005, 0.0002),
            "gemini-2.5-flash-lite": (0.00005, 0.0002),
            "mixtral-8x7b-32768": (0.0002, 0.0002),
            "llama3.1-8b": (0.0001, 0.0001),
            "meta-llama/llama-3-8b-instruct:free": (0.0, 0.0),
            "qwen3:8b": (0.0, 0.0)
        }
        in_rate, out_rate = cost_map.get(model_name, (0.0001, 0.0004))
        input_cost = (input_tokens / 1000.0) * in_rate
        output_cost = (output_tokens / 1000.0) * out_rate
        total_cost = input_cost + output_cost

        status_str = "success" if result.get("type") != "error" else "error"
        res_text = result.get("text", "")
        if result.get("type") == "tool_call":
            res_text = json.dumps({"tool_name": result.get("tool_name"), "tool_args": result.get("tool_args")})

        try:
            PromptRun.objects.create(
                purpose=operation or "llm_generation",
                prompt_text=prompt,
                response_text=res_text,
                model_name=model_name,
                provider=provider_key,
                model=model_name,
                prompt_key=prompt_key or "",
                prompt_version=prompt_version,
                template_variables=template_variables or {},
                tokens_used=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                duration_ms=latency_ms,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
                cost_usd=total_cost,
                correlation_id=correlation_id,
                operation=operation,
                trace_id=trace_id,
                span_id=span_id,
                metadata=context_metadata,
                status=status_str,
                error_type=error_type,
                error_code=error_code,
                error_message=error_message,
                provider_status_code=provider_status_code,
                retry_count=retry_count,
                started_at=started_at,
                completed_at=timezone.now()
            )
        except Exception as db_err:
            logger.error(f"Failed to record PromptRun telemetry in DB: {db_err}")

        if span:
            try:
                if status_str == "error":
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

    def _report_failure_with_cooldown(self, config: ModelConfig, cat: LLMErrorCategory, status_code: Optional[int]):
        """Blacklist provider/model with a custom cooldown period based on error category."""
        blacklist_duration = 120
        if cat == LLMErrorCategory.QUOTA_EXCEEDED:
            blacklist_duration = 43200  # 12 hours (done for the day)
        elif cat == LLMErrorCategory.RATE_LIMITED:
            blacklist_duration = 600    # 10 minutes
            
        self.health_monitor.report_failure(
            config.provider_key,
            config.model_name,
            status_code=status_code,
            blacklist_duration=blacklist_duration
        )

    def execute(self, request: LLMRequest) -> LLMResult:
        """
        Generic execution contract:
        1. Resolve prompt templates if prompt_key/system_prompt_key are provided.
        2. Select model pool based on operation + complexity.
        3. Try preferred pool models in priority order.
        4. Validate structured output if request.schema is specified.
        5. Execute global compatible-model fallback if preferred pool is exhausted.
        6. Return normalized LLMResult.
        """
        start_time = time.time()
        prompt = request.prompt or ""
        system_prompt = request.system_prompt or ""
        prompt_key = request.prompt_key
        system_prompt_key = request.system_prompt_key
        prompt_version = request.prompt_version
        system_prompt_version = request.system_prompt_version
        variables = request.variables or {}
        prompt_obj = None

        if prompt_key:
            from llm.prompts import PromptRegistry
            try:
                rendered_data = PromptRegistry.render(prompt_key, variables=variables, version=prompt_version)
                prompt = rendered_data["rendered_prompt"]
                prompt_version = rendered_data["prompt_version"]
                prompt_key = rendered_data["prompt_key"]
                variables = rendered_data["variables"]
                from llm.models import LLMPrompt
                prompt_obj = LLMPrompt.objects.filter(key=prompt_key, version=prompt_version).first()
            except Exception as e:
                logger.warning(f"Failed to resolve prompt_key '{prompt_key}': {e}")

        if system_prompt_key:
            from llm.prompts import PromptRegistry
            try:
                rendered_sys = PromptRegistry.render(system_prompt_key, variables=variables, version=system_prompt_version)
                system_prompt = rendered_sys["rendered_prompt"]
                system_prompt_version = rendered_sys["prompt_version"]
                system_prompt_key = rendered_sys["prompt_key"]
            except Exception as e:
                logger.warning(f"Failed to resolve system_prompt_key '{system_prompt_key}': {e}")

        # Get candidate models from preferred pool
        candidate_configs = get_pool_models(request.complexity)
        attempt_count = 0
        attempted_model_keys: Set[str] = set()
        last_error_category = LLMErrorCategory.UNKNOWN
        last_error_message = "No candidates available."

        # Preferred Pool Attempt Loop
        for config in candidate_configs:
            if config.model_name in attempted_model_keys:
                continue
            if not self.health_monitor.is_healthy(config.provider_key, config.model_name):
                logger.info(f"Provider/model '{config.provider_key}:{config.model_name}' is blacklisted/cooldown, skipping.")
                continue

            attempted_model_keys.add(config.model_name)
            attempt_count += 1
            adapter = self._get_adapter_for_model(config)

            logger.info(f"[LLM Router] Pool attempt {attempt_count}: model='{config.model_name}' (provider='{config.provider_key}')")
            res_dict = self._generate_with_logging(
                provider_key=config.provider_key,
                adapter=adapter,
                prompt=prompt,
                system_prompt=system_prompt,
                tools=request.tools or [],
                prompt_key=prompt_key,
                prompt_version=prompt_version,
                system_prompt_key=system_prompt_key,
                system_prompt_version=system_prompt_version,
                template_variables=variables,
                prompt_obj=prompt_obj
            )

            if res_dict.get("type") == "error":
                status_code = res_dict.get("status_code")
                err_text = res_dict.get("text", "")
                cat = classify_error(status_code, err_text)
                last_error_category = cat
                last_error_message = err_text
                self._report_failure_with_cooldown(config, cat, status_code=status_code)

                if cat not in RETRYABLE_ERROR_CATEGORIES:
                    logger.warning(f"Non-retryable error ({cat}) on model '{config.model_name}': {err_text}")
                    return LLMResult(
                        output=None,
                        raw_text=err_text,
                        model=config.model_name,
                        provider=config.provider_key,
                        attempts=attempt_count,
                        status="error",
                        error_category=cat,
                        error_message=err_text,
                        latency_ms=int((time.time() - start_time) * 1000)
                    )
                continue

            # Model produced text or tool call -> report success to health monitor
            self.health_monitor.report_success(config.provider_key, config.model_name)
            raw_text = res_dict.get("text", "")
            usage = {
                "prompt_tokens": res_dict.get("prompt_tokens", 0),
                "completion_tokens": res_dict.get("completion_tokens", 0),
                "total_tokens": res_dict.get("total_tokens", 0),
            }

            # Handle Structured Output Validation if schema is provided
            if request.schema:
                try:
                    parsed_json = json.loads(raw_text)
                    validated_output = request.schema.model_validate(parsed_json)
                    return LLMResult(
                        output=validated_output,
                        raw_text=raw_text,
                        model=config.model_name,
                        provider=config.provider_key,
                        attempts=attempt_count,
                        status="success",
                        usage=usage,
                        latency_ms=int((time.time() - start_time) * 1000)
                    )
                except (json.JSONDecodeError, ValidationError) as schema_err:
                    last_error_category = LLMErrorCategory.SCHEMA_VALIDATION_FAILED
                    last_error_message = f"Schema validation failed: {str(schema_err)}"
                    logger.warning(f"Schema validation failed for model '{config.model_name}': {schema_err}")
                    continue
            else:
                output = raw_text
                if res_dict.get("type") == "tool_call":
                    output = {"tool_name": res_dict.get("tool_name"), "tool_args": res_dict.get("tool_args")}
                return LLMResult(
                    output=output,
                    raw_text=raw_text,
                    model=config.model_name,
                    provider=config.provider_key,
                    attempts=attempt_count,
                    status="success",
                    usage=usage,
                    latency_ms=int((time.time() - start_time) * 1000)
                )

        # Global Fallback Loop if preferred pool is exhausted
        logger.info("[LLM Router] Preferred pool exhausted. Triggering global compatible-model fallback...")
        fallback_configs = get_global_fallback_models(exclude_keys=attempted_model_keys)

        for config in fallback_configs:
            if not self.health_monitor.is_healthy(config.provider_key, config.model_name):
                continue

            attempted_model_keys.add(config.model_name)
            attempt_count += 1
            adapter = self._get_adapter_for_model(config)

            logger.info(f"[LLM Router] Global fallback attempt {attempt_count}: model='{config.model_name}' (provider='{config.provider_key}')")
            res_dict = self._generate_with_logging(
                provider_key=config.provider_key,
                adapter=adapter,
                prompt=prompt,
                system_prompt=system_prompt,
                tools=request.tools or [],
                prompt_key=prompt_key,
                prompt_version=prompt_version,
                system_prompt_key=system_prompt_key,
                system_prompt_version=system_prompt_version,
                template_variables=variables,
                prompt_obj=prompt_obj
            )

            if res_dict.get("type") == "error":
                status_code = res_dict.get("status_code")
                err_text = res_dict.get("text", "")
                cat = classify_error(status_code, err_text)
                last_error_category = cat
                last_error_message = err_text
                self._report_failure_with_cooldown(config, cat, status_code=status_code)
                continue

            self.health_monitor.report_success(config.provider_key, config.model_name)
            raw_text = res_dict.get("text", "")
            usage = {
                "prompt_tokens": res_dict.get("prompt_tokens", 0),
                "completion_tokens": res_dict.get("completion_tokens", 0),
                "total_tokens": res_dict.get("total_tokens", 0),
            }

            if request.schema:
                try:
                    parsed_json = json.loads(raw_text)
                    validated_output = request.schema.model_validate(parsed_json)
                    return LLMResult(
                        output=validated_output,
                        raw_text=raw_text,
                        model=config.model_name,
                        provider=config.provider_key,
                        attempts=attempt_count,
                        status="success",
                        usage=usage,
                        latency_ms=int((time.time() - start_time) * 1000)
                    )
                except (json.JSONDecodeError, ValidationError) as schema_err:
                    last_error_category = LLMErrorCategory.SCHEMA_VALIDATION_FAILED
                    last_error_message = f"Schema validation failed: {str(schema_err)}"
                    continue
            else:
                output = raw_text
                if res_dict.get("type") == "tool_call":
                    output = {"tool_name": res_dict.get("tool_name"), "tool_args": res_dict.get("tool_args")}
                return LLMResult(
                    output=output,
                    raw_text=raw_text,
                    model=config.model_name,
                    provider=config.provider_key,
                    attempts=attempt_count,
                    status="success",
                    usage=usage,
                    latency_ms=int((time.time() - start_time) * 1000)
                )

        # All candidates failed
        return LLMResult(
            output=None,
            raw_text="",
            model="",
            provider="",
            attempts=attempt_count,
            status="error",
            error_category=last_error_category,
            error_message=last_error_message,
            latency_ms=int((time.time() - start_time) * 1000)
        )

    def generate(
        self,
        prompt: str = "",
        system_prompt: str = "",
        tools: list = None,
        prompt_key: str = None,
        system_prompt_key: str = None,
        prompt_version: int = None,
        system_prompt_version: int = None,
        template_variables: dict = None
    ) -> Dict[str, Any]:
        """
        Backward-compatible wrapper mapping generate(...) calls to the generic execute(...) contract.
        """
        score = calculate_complexity_score(prompt, tools)
        tier_str = get_complexity_tier(score)
        complexity = LLMComplexity.COMPLEX if tier_str == "critical" else (
            LLMComplexity.STANDARD if tier_str == "medium" else LLMComplexity.SIMPLE
        )

        req = LLMRequest(
            operation=LLMOperation.GENERATE,
            complexity=complexity,
            prompt=prompt,
            system_prompt=system_prompt,
            prompt_key=prompt_key,
            system_prompt_key=system_prompt_key,
            prompt_version=prompt_version,
            system_prompt_version=system_prompt_version,
            variables=template_variables,
            tools=tools
        )
        res = self.execute(req)

        if not res.is_success():
            return {
                "type": "error",
                "text": res.error_message or "Router failed to generate response.",
                "status_code": 500
            }

        if isinstance(res.output, dict) and "tool_name" in res.output:
            return {
                "type": "tool_call",
                "tool_name": res.output.get("tool_name"),
                "tool_args": res.output.get("tool_args", {}),
                "prompt_tokens": res.usage.get("prompt_tokens", 0),
                "completion_tokens": res.usage.get("completion_tokens", 0),
                "total_tokens": res.usage.get("total_tokens", 0),
                "provider": res.provider,
                "model": res.model
            }

        out_text = res.raw_text if isinstance(res.output, BaseModel) else str(res.output)
        return {
            "type": "text",
            "text": out_text,
            "prompt_tokens": res.usage.get("prompt_tokens", 0),
            "completion_tokens": res.usage.get("completion_tokens", 0),
            "total_tokens": res.usage.get("total_tokens", 0),
            "provider": res.provider,
            "model": res.model
        }

    def get_providers_status(self) -> Dict[str, Any]:
        """Return full provider health, model names, and priority waterlines."""
        provider_details = []
        for key, adapter in self.adapters.items():
            model_name = getattr(adapter, "model_name", "unknown")
            is_healthy = self.health_monitor.is_healthy(key)
            has_key = True
            status_label = "healthy" if is_healthy else "cooldown"
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

# Global Service Singleton
llm_service = IntelligentRouter()
router = llm_service
