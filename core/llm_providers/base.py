from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseLLMProvider(ABC):
    """Base interface for all LLM providers."""
    
    @abstractmethod
    def generate(self, prompt: str, system_prompt: str = "", tools: list = None) -> Dict[str, Any]:
        """
        Generate a response given a prompt.
        
        Args:
            prompt (str): The user prompt to send to the LLM.
            system_prompt (str): The system instructions.
            tools (list): Optional list of tool schemas.
            
        Returns:
            Dict[str, Any]: A structured response containing the text and/or tool calls.
        """
        pass
