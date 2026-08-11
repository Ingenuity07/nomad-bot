import os
from llm.interfaces import OpenAICompatibleAdapter

class OllamaAdapter(OpenAICompatibleAdapter):
    """Adapter for local Ollama server running OpenAI-compatible API."""
    def __init__(self, model_name: str = "qwen3:8b"):
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        api_url = f"{host.rstrip('/')}/v1/chat/completions"
        # pass dummy non-empty api_key to satisfy parent class checks
        super().__init__(api_key="ollama", api_url=api_url, model_name=model_name)

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None):
        return super().generate(prompt, system_prompt, tools)
