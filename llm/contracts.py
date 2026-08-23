from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Type
from pydantic import BaseModel
from llm.enums import LLMOperation, LLMComplexity, LLMErrorCategory

@dataclass
class LLMRequest:
    operation: LLMOperation = LLMOperation.GENERATE
    complexity: LLMComplexity = LLMComplexity.STANDARD
    prompt_key: Optional[str] = None
    system_prompt_key: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    schema: Optional[Type[BaseModel]] = None
    tools: Optional[List[Any]] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)
    prompt_version: Optional[int] = None
    system_prompt_version: Optional[int] = None

@dataclass
class LLMResult:
    output: Any
    raw_text: str = ""
    model: str = ""
    provider: str = ""
    attempts: int = 1
    status: str = "success"  # "success" or "error"
    error_category: Optional[LLMErrorCategory] = None
    error_message: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    latency_ms: float = 0.0

    def is_success(self) -> bool:
        return self.status == "success"
