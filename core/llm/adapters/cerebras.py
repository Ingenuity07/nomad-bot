import os
from core.llm.interfaces import OpenAICompatibleAdapter

class CerebrasAdapter(OpenAICompatibleAdapter):
    """Adapter for Cerebras Inference API."""
    def __init__(self, model_name: str = "llama3.1-8b"):
        api_key = os.environ.get("CEREBRAS_API_KEY")
        api_url = "https://api.cerebras.ai/v1/chat/completions"
        super().__init__(api_key=api_key, api_url=api_url, model_name=model_name)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None):
        return super().generate(prompt, system_prompt, tools)
