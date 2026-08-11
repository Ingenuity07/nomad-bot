import json
from typing import List, Dict, Any, Callable
from collections import Counter
from .base import BaseAgent

class LoopState:
    """Tracks the history of tool calls within a single execution loop to detect stuck loops."""
    def __init__(self):
        self.tool_history: List[tuple[str, str]] = []
        self.error_streak = 0
        
    def record_tool_call(self, tool_name: str, tool_args: dict, success: bool):
        key_parts = []
        for k in ("repo", "query", "path", "file_path", "pr_number", "ref"):
            if k in tool_args:
                key_parts.append(f"{k}={tool_args[k]}")
        key_str = ",".join(key_parts) if key_parts else str(tool_args)
        self.tool_history.append((tool_name, key_str))
        
        if success:
            self.error_streak = 0
        else:
            self.error_streak += 1
            
    def is_stuck(self) -> bool:
        if len(self.tool_history) < 3:
            return False
        # If the same tool and arguments are called 3+ times in the last 5 calls
        recent = self.tool_history[-5:]
        counts = Counter(recent)
        for (tool_name, key_str), count in counts.items():
            if count >= 3:
                return True
        return False


def _compress_old_tool_results(messages: List[Dict[str, Any]], current_iteration: int):
    """
    Trims tool results from 2+ iterations ago that are longer than 50 lines,
    retaining the first 20 and last 10 lines.
    """
    if current_iteration < 2:
        return
        
    tool_iteration = -1
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_call"):
            tool_iteration += 1
        elif msg.get("role") == "tool":
            content = msg.get("content", "")
            lines = content.split("\n")
            
            if tool_iteration < current_iteration - 1 and len(lines) > 50:
                head = "\n".join(lines[:20])
                tail = "\n".join(lines[-10:])
                omitted = len(lines) - 20 - 10
                msg["content"] = (
                    f"{head}\n"
                    f"... ({omitted} lines omitted, full output recorded in audit trace) ...\n"
                    f"{tail}"
                )


class ResearchAgent(BaseAgent):
    """
    Agent responsible for research tasks, capable of using tools to read files and search.
    Upgraded with LoopState tracking (stuck detection) and context compression.
    """
    
    @property
    def name(self) -> str:
        return "ResearchAgent"
        
    @property
    def system_prompt(self) -> str:
        return (
            "You are a Research Agent. Your goal is to answer user queries accurately. "
            "You can use tools to read local files, query GitHub, or search. "
            "If you need to use a tool, return a JSON object with 'tool_name' and 'tool_args'. "
            "Once you have all the information, return your final answer in a JSON object with 'response'."
        )
        
    def execute(self, prompt: str, conversation_history: list = None, **kwargs) -> str:
        on_tool_execution = kwargs.get("on_tool_execution")
        tools_schema = self.tool_registry.get_tool_schemas() if self.tool_registry else None
        
        # Build structured message state for the execution loop
        messages = [{"role": "user", "content": prompt}]
        loop_state = LoopState()
        
        self.accumulated_prompt_tokens = 0
        self.accumulated_completion_tokens = 0
        
        from llm.tools.implementations.browser_tool import PlaywrightBrowser
        
        try:
            max_iterations = 15
            for iteration in range(max_iterations):
                # Apply context compression to older turns
                _compress_old_tool_results(messages, iteration)
                
                # Format history + current turns into a single prompt for Gemini CLI/API provider
                prompt_parts = []
                if conversation_history:
                    prompt_parts.append("Conversation History:")
                    for msg in conversation_history:
                        prompt_parts.append(f"{msg['role']}: {msg['content']}")
                    prompt_parts.append("")
                    
                for msg in messages:
                    if msg["role"] == "user":
                        prompt_parts.append(f"User: {msg['content']}")
                    elif msg["role"] == "assistant":
                        content = msg.get("content") or ""
                        if msg.get("tool_call"):
                            content += f"\nCalling tool: {msg['tool_call']['name']} with {msg['tool_call']['args']}"
                        prompt_parts.append(f"Assistant: {content}")
                    elif msg["role"] == "tool":
                        prompt_parts.append(f"Tool '{msg.get('tool_name')}' returned:\n{msg['content']}")
                        
                current_prompt = "\n".join(prompt_parts)
                
                # Call provider
                result = self.provider.generate(
                    prompt=current_prompt,
                    system_prompt=self.system_prompt,
                    tools=tools_schema
                )
                
                # Accumulate tokens
                self.accumulated_prompt_tokens += result.get("prompt_tokens", 0)
                self.accumulated_completion_tokens += result.get("completion_tokens", 0)
                
                if result.get("type") == "tool_call":
                    tool_name = result["tool_name"]
                    tool_args = result.get("tool_args", {})
                    
                    # Append assistant call to message log
                    messages.append({
                        "role": "assistant",
                        "content": result.get("text", ""),
                        "tool_call": {"name": tool_name, "args": tool_args}
                    })
                    
                    # Check for stuck loop
                    loop_state.record_tool_call(tool_name, tool_args, success=True)
                    if loop_state.is_stuck():
                        # Inject stuck warning prompt and record failure in DB
                        intervention = (
                            "STUCK DETECTED: You have called the same tool multiple times with similar arguments.\n"
                            "You MUST change your approach: try a different tool, different search terms, or provide your final response."
                        )
                        messages.append({"role": "user", "content": intervention})
                        if on_tool_execution:
                            on_tool_execution(tool_name, tool_args, f"Error: stuck loop detected. {intervention}", "error")
                        continue
                    
                    try:
                        tool = self.tool_registry.get_tool(tool_name)
                        tool_result = tool.execute(**tool_args)
                        tool_success = not (str(tool_result).startswith("Error") or str(tool_result).startswith("Tool error"))
                        status = "success" if tool_success else "error"
                        
                        # Record execution callback
                        if on_tool_execution:
                            on_tool_execution(tool_name, tool_args, tool_result, status)
                            
                        messages.append({
                            "role": "tool",
                            "tool_name": tool_name,
                            "content": str(tool_result)
                        })
                    except Exception as e:
                        tool_result = f"Tool execution failed: {str(e)}"
                        if on_tool_execution:
                            on_tool_execution(tool_name, tool_args, tool_result, "error")
                        messages.append({
                            "role": "tool",
                            "tool_name": tool_name,
                            "content": tool_result
                        })
                else:
                    return result.get("text", str(result))
                    
            return "Error: Agent reached maximum iterations without providing a final response."
        finally:
            PlaywrightBrowser.close()
