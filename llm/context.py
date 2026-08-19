import contextvars
from typing import Dict, Any, Optional

# ContextVar storing the current thread/async request context dictionary
_llm_request_context = contextvars.ContextVar("llm_request_context", default=None)

class LLMRequestContext:
    def __init__(
        self,
        correlation_id: str = "",
        operation: str = "",
        user_id: str = "",
        workspace_id: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.data = {
            "correlation_id": correlation_id,
            "operation": operation,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "metadata": metadata or {}
        }
        self.token = None

    def __enter__(self):
        # Merge with existing parent context if present
        parent = _llm_request_context.get()
        if parent:
            merged = parent.copy()
            # Update fields that are set
            for k, v in self.data.items():
                if k != "metadata":
                    if v:
                        merged[k] = v
                else:
                    # Deep merge metadata
                    merged_meta = parent.get("metadata", {}).copy()
                    merged_meta.update(self.data.get("metadata") or {})
                    merged["metadata"] = merged_meta
            self.data = merged
            
        self.token = _llm_request_context.set(self.data)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.token:
            _llm_request_context.reset(self.token)

    @staticmethod
    def get_current() -> Optional[Dict[str, Any]]:
        """Retrieve the currently active context dictionary."""
        return _llm_request_context.get()

    @staticmethod
    def get_value(key: str, default: Any = None) -> Any:
        """Retrieve a specific key from the active context."""
        ctx = _llm_request_context.get()
        if ctx:
            return ctx.get(key, default)
        return default
