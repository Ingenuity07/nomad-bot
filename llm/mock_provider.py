from typing import Dict, Any
from .base import BaseLLMProvider

class MockLLMProvider(BaseLLMProvider):
    """
    A mock provider to test the system when external APIs fail.
    This proves that the core architecture is decoupled from the LLM implementation!
    """
    
    def generate(self, prompt: str, system_prompt: str = "", tools: list = None) -> Dict[str, Any]:
        return {
            "type": "text",
            "text": f"Hello! I am the Mock LLM Provider. The database, orchestrator, and API are working perfectly. You said: '{prompt}'"
        }
