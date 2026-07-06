from typing import Dict, Any
from .base import BaseTool

class ToolRegistry:
    """Registry to discover and manage tools."""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        
    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool
        
    def get_tool(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise ValueError(f"Tool {name} not found in registry.")
        return self._tools[name]
        
    def get_all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())
        
    def get_tool_schemas(self) -> list[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }
            for tool in self._tools.values()
        ]
