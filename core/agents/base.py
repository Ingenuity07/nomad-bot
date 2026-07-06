from abc import ABC, abstractmethod
from ..llm_providers.base import BaseLLMProvider
from ..tools.registry import ToolRegistry

class BaseAgent(ABC):
    """Base interface for all agents."""
    
    def __init__(self, provider: BaseLLMProvider, tool_registry: ToolRegistry = None):
        self.provider = provider
        self.tool_registry = tool_registry
        
    @property
    @abstractmethod
    def name(self) -> str:
        pass
        
    @property
    @abstractmethod
    def system_prompt(self) -> str:
        pass
        
    @abstractmethod
    def execute(self, prompt: str, conversation_history: list = None, **kwargs) -> str:
        """
        Execute a task given a prompt and conversation history.
        Returns the final response string.
        """
        pass
