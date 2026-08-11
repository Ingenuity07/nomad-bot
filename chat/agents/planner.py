import json
import logging
from llm.gemini_api import GeminiAPIProvider

logger = logging.getLogger(__name__)

class PlannerAgent:
    """
    High-level Planner Agent responsible for breaking down a user request
    into a sequential list of structured workflow goals (a plan).
    """
    def __init__(self, provider: GeminiAPIProvider):
        self.provider = provider

    def generate_plan(self, prompt: str) -> list[str]:
        system_prompt = (
            "You are a high-level job application workflow planner. Your goal is to break down a user's request "
            "into a sequence of high-level goals. The available goals are:\n"
            "- 'search_jobs': Search for jobs matching the query on Lever, Greenhouse, or general boards.\n"
            "- 'scrape_job': Scrape the details and text content of a specific job post URL.\n"
            "- 'tailor_resume': Tailor/customize the user's base resume against the scraped job description.\n"
            "- 'fill_application': Navigate to and fill out the online application form with the user's credentials.\n"
            "- 'submit_application': Submit the application form (requires human approval first).\n\n"
            "Return a JSON object containing a 'plan' list of strings. Example:\n"
            "{\"plan\": [\"search_jobs\", \"scrape_job\", \"tailor_resume\", \"fill_application\", \"submit_application\"]}"
        )

        response = self.provider.generate(
            prompt=f"User request: {prompt}",
            system_prompt=system_prompt,
            tools=None
        )

        text = response.get("text", "")
        try:
            # Clean code block backticks if present
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            data = json.loads(text.strip())
            return data.get("plan", [])
        except Exception as e:
            logger.error(f"Error parsing planner response: {str(e)}. Raw text: {text}")
            # Semantic fallback: if it's a job/application query, use the full pipeline. Otherwise, generic task.
            clean_prompt = prompt.lower()
            if any(w in clean_prompt for w in ["apply", "job", "resume", "lever", "greenhouse"]):
                return ["search_jobs", "scrape_job", "tailor_resume", "fill_application", "submit_application"]
            return ["general_task"]
