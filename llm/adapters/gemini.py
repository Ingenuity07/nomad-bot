import os
from llm.gemini_api import GeminiAPIProvider
from llm.interfaces import RouterLLMProvider

class GeminiAdapter(RouterLLMProvider):
    """Adapter for Google AI Studio Gemini API."""
    def __init__(self, model_name: str = None):
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
        self.provider = GeminiAPIProvider(model=self.model_name)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None):
        return self.provider.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
