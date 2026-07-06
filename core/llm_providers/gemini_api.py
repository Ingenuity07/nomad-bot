import json
import requests
from typing import Dict, Any
from .base import BaseLLMProvider

class GeminiAPIProvider(BaseLLMProvider):
    """
    LLM Provider that uses the official Gemini REST API.
    """
    
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None) -> Dict[str, Any]:
        headers = {
            'Content-Type': 'application/json',
            'x-goog-api-key': self.api_key
        }
        
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"System Instruction: {system_prompt}\n\nUser: {prompt}"
            
        if tools:
            tool_instruction = (
                "You have access to the following tools:\n"
                f"{json.dumps(tools, indent=2)}\n"
                "If you need to use a tool, output a JSON object with 'tool_name' and 'tool_args'. "
                "Otherwise, output a JSON object with 'response' containing your final answer."
            )
            full_prompt = f"{tool_instruction}\n\n{full_prompt}"
            
        payload = {
            "contents": [{
                "parts": [{"text": full_prompt}]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        try:
            response = requests.post(self.url, headers=headers, json=payload)
            response.raise_for_status()
            
            data = response.json()
            try:
                output_text = data['candidates'][0]['content']['parts'][0]['text']
            except (KeyError, IndexError):
                return {"type": "error", "text": "Unexpected response format from Gemini API."}
                
            # Extract token usage metadata
            usage_meta = data.get("usageMetadata", {})
            prompt_tokens = usage_meta.get("promptTokenCount", 0)
            completion_tokens = usage_meta.get("candidatesTokenCount", 0)
                
            try:
                parsed_output = json.loads(output_text)
                if 'tool_name' in parsed_output:
                    return {
                        "type": "tool_call",
                        "tool_name": parsed_output["tool_name"],
                        "tool_args": parsed_output.get("tool_args", {}),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens
                    }
                else:
                    return {
                        "type": "text",
                        "text": parsed_output.get("response", output_text),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens
                    }
            except json.JSONDecodeError:
                return {
                    "type": "text",
                    "text": output_text,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "type": "error",
                "text": f"Error calling Gemini REST API: {str(e)}"
            }
