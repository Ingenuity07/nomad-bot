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
        
    @property
    def input_schema(self) -> Any:
        """Pydantic input schema class, if defined."""
        return None

    @property
    def output_schema(self) -> Any:
        """Pydantic output schema class, if defined."""
        return None

    @property
    def category(self) -> str:
        """Category category name (e.g. 'discovery', 'research')."""
        return "general"

    @property
    def read_only(self) -> bool:
        """Indicates if this is a read-only query tool or state-mutating tool."""
        return True

    @property
    def requires_approval(self) -> bool:
        """Indicates if this tool requires explicit human approval before running."""
        return False

    @property
    def output_description(self) -> str:
        """Textual description of the returned data shape."""
        return ""
        
    @abstractmethod
    def execute(self, **kwargs) -> Any:
        pass
