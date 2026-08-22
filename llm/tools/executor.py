import time
import logging
import json
from typing import Optional, Any
from pydantic import ValidationError

from .context import ToolContext
from .result import ToolResult, ToolError
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

class ToolExecutor:
    """
    Central execution boundary for all tools.
    Validates input schemas, handles authorization/policies, monitors execution latency,
    normalizes provider results/errors, and logs audits to the database.
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def _check_policy(self, tool_name: str, context: Optional[ToolContext]) -> None:
        """Enforces policies like read_only constraints and approval gates."""
        if not context:
            return

        try:
            tool = self.registry.get_tool(tool_name)
        except ValueError:
            return

        # 1. Enforce Read-Only Constraint
        is_read_only = context.metadata.get("read_only", False)
        if is_read_only and not getattr(tool, "read_only", True):
            raise PermissionError(
                f"Execution of '{tool_name}' blocked: read-only policy is active."
            )

        # 2. Enforce Approval Gates
        require_approval = getattr(tool, "requires_approval", False) or context.metadata.get("require_approval", False)
        if require_approval:
            is_approved = context.metadata.get("is_approved", False)
            if not is_approved:
                raise PermissionError(
                    f"Execution of '{tool_name}' blocked: requires user approval."
                )

    def execute(self, tool_name: str, arguments: dict, context: Optional[ToolContext] = None) -> ToolResult:
        start_time = time.perf_counter()
        provider_name = None
        
        try:
            # 1. Resolve tool from registry
            tool = self.registry.get_tool(tool_name)
            
            # 2. Check Execution Policies
            self._check_policy(tool_name, context)
            
            # 3. Validate Inputs
            validated_args = arguments
            if tool.input_schema:
                try:
                    # Parse and validate via Pydantic schema
                    validated_model = tool.input_schema(**arguments)
                    # Pydantic v1/v2 backward compatibility fallback
                    if hasattr(validated_model, "model_dump"):
                        validated_args = validated_model.model_dump()
                    else:
                        validated_args = validated_model.dict()
                except ValidationError as val_err:
                    duration_ms = int((time.perf_counter() - start_time) * 1000)
                    result = ToolResult(
                        success=False,
                        data=None,
                        error=ToolError(
                            code="VALIDATION_FAILED",
                            message=f"Input validation failed: {str(val_err)}",
                            retryable=False,
                            details=val_err.errors()
                        ),
                        provider=None,
                        tool_name=tool_name,
                        duration_ms=duration_ms
                    )
                    self._audit_execution(tool_name, arguments, result, context)
                    return result

            logger.debug(
                "TOOL_REQUEST tool=%s run_id=%s arguments=%s",
                tool_name,
                getattr(context, "run_id", None) if context else None,
                json.dumps(self._sanitize_data(validated_args), default=str, ensure_ascii=False),
            )

            # 4. Execute the Tool
            # Inject context if accepted/expected by the tool
            execute_kwargs = {**validated_args}
            if context:
                execute_kwargs["context"] = context
                
            tool_output = tool.execute(**execute_kwargs)
            duration_ms = int((time.perf_counter() - start_time) * 1000)

            # 5. Normalize results
            if isinstance(tool_output, ToolResult):
                result = tool_output
                result.duration_ms = duration_ms
            else:
                # Wrap legacy tool output format
                result = ToolResult(
                    success=True,
                    data=tool_output,
                    provider=getattr(tool, "provider", None),
                    tool_name=tool_name,
                    duration_ms=duration_ms
                )

        except Exception as err:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            # Map exception to a structured ToolError
            err_msg = str(err)
            err_code = "INTERNAL_ERROR"
            retryable = False

            if isinstance(err, PermissionError):
                err_code = "POLICY_BLOCKED"
            elif isinstance(err, (TimeoutError, LookupError)) or "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                err_code = "TIMEOUT"
                retryable = True
            elif "rate limit" in err_msg.lower() or "429" in err_msg:
                err_code = "RATE_LIMITED"
                retryable = True
            elif "unauthorized" in err_msg.lower() or "forbidden" in err_msg.lower():
                err_code = "AUTHENTICATION_FAILED"

            result = ToolResult(
                success=False,
                data=None,
                error=ToolError(
                    code=err_code,
                    message=err_msg,
                    retryable=retryable
                ),
                provider=provider_name,
                tool_name=tool_name,
                duration_ms=duration_ms
            )

        # 6. Audit Logging to the Database
        output_val = result.data if result.success else (result.error.model_dump() if result.error else {})
        logger.debug(
            "TOOL_RESPONSE tool=%s provider=%s success=%s duration_ms=%s data=%s",
            tool_name,
            result.provider,
            result.success,
            result.duration_ms,
            json.dumps(self._sanitize_data(output_val), default=str, ensure_ascii=False),
        )
        self._audit_execution(tool_name, arguments, result, context)

        return result

    def _sanitize_data(self, val: Any) -> Any:
        """Recursively sanitizes sensitive keys from dictionary inputs/outputs."""
        if isinstance(val, dict):
            sanitized = {}
            for k, v in val.items():
                k_lower = k.lower()
                if any(secret in k_lower for secret in ["key", "token", "password", "secret", "auth", "cookie", "passphrase"]):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = self._sanitize_data(v)
            return sanitized
        elif isinstance(val, list):
            return [self._sanitize_data(item) for item in val]
        return val

    def _audit_execution(self, tool_name: str, arguments: dict, result: ToolResult, context: Optional[ToolContext]) -> None:
        # Chat agent run auditing removed in prospecting-only mode
        pass

        # Prospecting discovery runs use their run_id as a durable correlation
        # key. Keep this independent of the chat AgentRun audit above.
        discovery_run_id = getattr(context, "run_id", None) if context else None
        if discovery_run_id:
            try:
                from prospecting.discovery.tracing import DiscoveryTraceRecorder
                DiscoveryTraceRecorder(str(discovery_run_id)).record_tool_execution(
                    tool_name,
                    self._sanitize_data(arguments),
                    result,
                )
            except Exception as trace_err:
                logger.error(
                    "Failed to record discovery tool trace for run %s: %s",
                    discovery_run_id,
                    trace_err,
                )
