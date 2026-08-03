import json
import requests
from typing import Dict, Any
from core.llm_providers.base import BaseLLMProvider

class RouterLLMProvider(BaseLLMProvider):
    """Specific base for routed model providers, keeping the standard signature."""
    pass

class OpenAICompatibleAdapter(RouterLLMProvider):
    """Generic adapter for OpenAI-compatible completions endpoints."""
    def __init__(self, api_key: str, api_url: str, model_name: str):
        self.api_key = api_key
        self.api_url = api_url
        self.model_name = model_name

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None) -> Dict[str, Any]:
        if not self.api_key:
            return {"type": "error", "status_code": 401, "text": "API key missing for provider."}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        full_prompt = prompt
        if tools:
            tool_instruction = (
                "You have access to the following tools:\n"
                f"{json.dumps(tools, indent=2)}\n"
                "If you need to use a tool, output a JSON object with 'tool_name' and 'tool_args'. "
                "Otherwise, output a JSON object with 'response' containing your final answer."
            )
            full_prompt = f"{tool_instruction}\n\n{full_prompt}"

        messages.append({"role": "user", "content": full_prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"}
        }

        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()

            try:
                output_text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                return {"type": "error", "status_code": 500, "text": "Unexpected response format from API."}

            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens") or max(1, len(full_prompt) // 4)
            completion_tokens = usage.get("completion_tokens") or max(1, len(output_text) // 4)
            total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)

            try:
                parsed_output = json.loads(output_text)
                if isinstance(parsed_output, dict) and 'tool_name' in parsed_output:
                    return {
                        "type": "tool_call",
                        "tool_name": parsed_output["tool_name"],
                        "tool_args": parsed_output.get("tool_args", {}),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens
                    }
                elif isinstance(parsed_output, dict):
                    return {
                        "type": "text",
                        "text": parsed_output.get("response", output_text),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens
                    }
                else:
                    return {
                        "type": "text",
                        "text": output_text,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens
                    }
            except json.JSONDecodeError:
                return {
                    "type": "text",
                    "text": output_text,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens
                }

        except requests.exceptions.RequestException as e:
            status_code = 500
            if e.response is not None:
                status_code = e.response.status_code
            return {
                "type": "error",
                "status_code": status_code,
                "text": f"API request failed: {str(e)}"
            }
