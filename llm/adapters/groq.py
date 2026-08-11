import os
from llm.interfaces import OpenAICompatibleAdapter

class GroqAdapter(OpenAICompatibleAdapter):
    """Adapter for Groq Cloud API."""
    def __init__(self, model_name: str = "mixtral-8x7b-32768"):
        api_key = os.environ.get("GROQ_API_KEY")
        api_url = "https://api.groq.com/openai/v1/chat/completions"
        super().__init__(api_key=api_key, api_url=api_url, model_name=model_name)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None):
        return super().generate(prompt, system_prompt, tools)
