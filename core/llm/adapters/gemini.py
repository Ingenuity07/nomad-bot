import os
from core.llm_providers.gemini_api import GeminiAPIProvider
from core.llm.interfaces import RouterLLMProvider

class GeminiAdapter(RouterLLMProvider):
    """Adapter for Google AI Studio Gemini API."""
    def __init__(self, model_name: str = None):
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.provider = GeminiAPIProvider(model=self.model_name)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None):
        return self.provider.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
