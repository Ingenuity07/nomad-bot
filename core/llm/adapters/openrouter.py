import os
from core.llm.interfaces import OpenAICompatibleAdapter

class OpenRouterAdapter(OpenAICompatibleAdapter):
    """Adapter for OpenRouter API."""
    def __init__(self, model_name: str = "meta-llama/llama-3-8b-instruct:free"):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        super().__init__(api_key=api_key, api_url=api_url, model_name=model_name)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None):
        return super().generate(prompt, system_prompt, tools)
