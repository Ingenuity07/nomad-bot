import json
import logging
from typing import List, Dict, Any
from llm.router import IntelligentRouter

logger = logging.getLogger(__name__)

OPTIMIZER_SYSTEM_PROMPT = """
You are the Nomad V3.5 Resume Bullet & Keyword Optimizer.
Your job is to rewrite specific bullet points in a resume to improve readability, action verb strength, and align them to missing target ATS keywords.

CRITICAL RULES:
1. NEVER invent any false credentials, metrics, or experiences.
2. Maintain the core factual meaning of the original bullet point.
3. Inject the requested keywords naturally.
4. Always output JSON in the following format:
{
  "optimized_bullets": [
    "Architected high-throughput API endpoints using Django, matching the keyword Python.",
    "Reduced telemetry parsing latency by 45% using optimized Redis memory structures."
  ]
}
"""

class BulletOptimizerAgent:
    """Optimizes weak bullet points by injecting keywords and reinforcing verb strength."""

    def __init__(self, provider=None):
        self.provider = provider or IntelligentRouter()

    def optimize_bullets(self, original_bullets: List[str], target_keywords: List[str]) -> List[str]:
        """Call LLM to rewrite bullet points with power verbs and target keywords."""
        if not original_bullets:
            return []
            
        prompt = (
            f"Original Bullet Points:\n{json.dumps(original_bullets, indent=2)}\n\n"
            f"Target Keywords to Inject:\n{json.dumps(target_keywords, indent=2)}\n\n"
            "Optimize these bullet points for maximum impact and keyword coverage."
        )

        result = self.provider.generate(
            prompt=prompt,
            system_prompt=OPTIMIZER_SYSTEM_PROMPT
        )

        if result.get("type") == "error":
            logger.warning(f"Bullet optimization failed: {result.get('text')}. Returning originals.")
            return original_bullets

        raw_text = result.get("text", "")
        try:
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
            data = json.loads(raw_text)
            return data.get("optimized_bullets", original_bullets)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse optimizer JSON: {e}. Raw text: {raw_text}")
            return original_bullets
