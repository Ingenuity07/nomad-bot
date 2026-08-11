import json
import subprocess
from typing import Dict, Any
from .base import BaseLLMProvider

class GeminiCLIProvider(BaseLLMProvider):
    """
    LLM Provider that uses the Gemini CLI via subprocess.
    Assumes `gemini` CLI is available in the environment.
    """
    
    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = model

    def generate(self, prompt: str, system_prompt: str = "", tools: list = None) -> Dict[str, Any]:
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\nUser: {prompt}"
            
        if tools:
            tool_instruction = (
                "You have access to the following tools:\n"
                f"{json.dumps(tools, indent=2)}\n"
                "If you need to use a tool, output a JSON object with 'tool_name' and 'tool_args'. "
                "Otherwise, output a JSON object with 'response' containing your final answer."
            )
            full_prompt = f"{tool_instruction}\n\n{full_prompt}"

        cmd = ["gemini", "--model", self.model, "--prompt", full_prompt]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = result.stdout.strip()
            
            try:
                # Find JSON block if the model wraps it in markdown
                if "```json" in output:
                    json_str = output.split("```json")[1].split("```")[0].strip()
                    parsed_output = json.loads(json_str)
                else:
                    parsed_output = json.loads(output)

                if isinstance(parsed_output, dict) and 'tool_name' in parsed_output:
                    return {
                        "type": "tool_call",
                        "tool_name": parsed_output["tool_name"],
                        "tool_args": parsed_output.get("tool_args", {})
                    }
                elif isinstance(parsed_output, dict):
                    return {
                        "type": "text",
                        "text": parsed_output.get("response", output)
                    }
                else:
                    return {
                        "type": "text",
                        "text": output
                    }
            except json.JSONDecodeError:
                return {
                    "type": "text",
                    "text": output
                }
                
        except subprocess.CalledProcessError as e:
            return {
                "type": "error",
                "text": f"Error calling Gemini CLI: {e.stderr}"
            }
