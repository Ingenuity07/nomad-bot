from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseTool(ABC):
    """Base interface for all tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
        
    @property
    @abstractmethod
    def description(self) -> str:
        pass
        
    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON schema for tool parameters."""
        pass
        
    @abstractmethod
    def execute(self, **kwargs) -> Any:
        pass
