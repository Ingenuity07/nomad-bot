from pydantic import BaseModel
from typing import Any, Optional, Dict

class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool
    details: Any = None

class ToolResult(BaseModel):
    success: bool
    data: Any
    error: Optional[ToolError] = None
    provider: Optional[str] = None
    tool_name: str
    duration_ms: int = 0
    metadata: Dict[str, Any] = {}
