from llm.tools.registry import ToolRegistry
from llm.tools.implementations.discovery_tools import (
    SearchCompaniesTool,
    SearchWebTool,
    CrawlWebsiteTool,
    ExtractContactDataTool,
)

class ProspectingToolOrchestrator:
    """Lightweight tool registry and execution platform for the prospecting app."""
    
    def __init__(self):
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(SearchCompaniesTool())
        self.tool_registry.register(SearchWebTool())
        self.tool_registry.register(CrawlWebsiteTool())
        self.tool_registry.register(ExtractContactDataTool())
