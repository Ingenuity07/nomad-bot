def calculate_complexity_score(prompt: str, tools: list = None) -> int:
    """
    Heuristically calculate complexity score for an incoming prompt and toolset.
    
    Rules:
    - Prompt Length: +3 if > 1000 chars, +6 if > 3000 chars, +10 if > 5000 chars
    - Contains Resume/CV/JD: +5
    - Contains PDF/document: +3
    - Contains Scraper/Browser commands: +2
    - Needs JSON/structured output: +1
    - Needs Reflection/Critic logic: +4
    - Needs Planning/Roadmap: +6
    - Tools: +3 if BrowserTool present, +2 if more than 3 tools are available
    """
    score = 0
    p_lower = prompt.lower()
    
    # Prompt length scoring
    p_len = len(prompt)
    if p_len > 5000:
        score += 10
    elif p_len > 3000:
        score += 6
    elif p_len > 1000:
        score += 3
        
    # Keyword detection
    if "resume" in p_lower or "cv" in p_lower or "job description" in p_lower or " jd " in p_lower:
        score += 5
    if "pdf" in p_lower or "document" in p_lower or "file" in p_lower:
        score += 3
    if "browser" in p_lower or "navigate" in p_lower or "scrape" in p_lower or "playwright" in p_lower:
        score += 2
    if "json" in p_lower or "format" in p_lower or "structure" in p_lower:
        score += 1
    if "reflect" in p_lower or "critic" in p_lower or "review" in p_lower or "validate" in p_lower:
        score += 4
    if "plan" in p_lower or "roadmap" in p_lower or "steps" in p_lower or "checklist" in p_lower:
        score += 6
        
    # Tool-based scoring
    if tools:
        if len(tools) > 3:
            score += 2
        for tool in tools:
            # Check if name is BrowserTool
            tool_name = ""
            if isinstance(tool, dict):
                tool_name = tool.get("name", "").lower()
            elif hasattr(tool, "name"):
                tool_name = getattr(tool, "name", "").lower()
            
            if "browser" in tool_name or "playwright" in tool_name:
                score += 3
                
    return score

def get_complexity_tier(score: int) -> str:
    """Map complexity score to simple, medium, or critical tier."""
    if score <= 5:
        return "simple"
    elif score <= 12:
        return "medium"
    else:
        return "critical"
