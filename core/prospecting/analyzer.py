import json
import logging
import requests
from bs4 import BeautifulSoup
from memory.models import LeadCompany, WebsiteAnalysis
from core.llm.router import IntelligentRouter

logger = logging.getLogger(__name__)

QUALIFICATION_SYSTEM_PROMPT = """
You are the VisiofyTech Lead Qualification AI Agent.
Your mandate is to analyze a company's website text and classify their business characteristics to determine if they need Route Optimization Software.

Analyze the text and extract the following properties in strict JSON format:
{
  "description": "Short summary of what this company does.",
  "has_delivery": true/false (Does the business offer delivery services of products or goods?),
  "has_scheduling": true/false (Does the business dispatch technicians, plumbers, cleaners, or schedule appointments at customer locations?),
  "needs_routing": true/false (Do they visit multiple client sites or operate a vehicle fleet daily?),
  "fleet_size_estimate": "1-5", "5-20", "20+", or "unknown" (Estimate based on description/text, default to "unknown"),
  "lead_score": 1-10 (10 = perfect fit like logistics/couriers/technicians, 1 = static office/store with no routing needs),
  "lead_score_reason": "Provide a brief explanation for the assigned score based on the text."
}

CRITICAL RULES:
1. ONLY return the valid JSON block. Do NOT add extra conversational text.
2. If the text does not contain enough info, assign a conservative score (e.g. 3) and default fields to false.
"""

class WebsiteAnalyzer:
    """Uses LLM to analyze company websites for route-optimization suitability."""

    def __init__(self, provider=None):
        self.provider = provider or IntelligentRouter()

    def analyze_website(self, company: LeadCompany) -> WebsiteAnalysis:
        if not company.website:
            logger.info(f"No website found for {company.name}. Creating generic low-score analysis.")
            analysis = WebsiteAnalysis.objects.create(
                company=company,
                description="No website resolved.",
                lead_score=3.0,
                lead_score_reason="Generic score assigned because no website was resolved for search verification."
            )
            return analysis

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        text_content = ""

        try:
            logger.info(f"Fetching website text for analysis: {company.website}")
            res = requests.get(company.website, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                # Remove script/style tags
                for s in soup(["script", "style", "meta", "noscript"]):
                    s.decompose()
                # Get clean text, trim spacing, limit size to fit LLM prompt context nicely
                text_content = " ".join(soup.get_text().split())[:8000]
        except Exception as e:
            logger.error(f"Failed to scrape homepage text for {company.name}: {e}")

        # Fallback if page download yielded no text
        if not text_content.strip():
            text_content = f"Company Name: {company.name}. Category: {company.category or 'Business'}."

        prompt = (
            f"Company Name: {company.name}\n"
            f"Sector/Category: {company.category}\n\n"
            f"Website Scraped Content:\n{text_content}\n\n"
            "Analyze and output the structured JSON qualification:"
        )

        result = self.provider.generate(
            prompt=prompt,
            system_prompt=QUALIFICATION_SYSTEM_PROMPT
        )

        # Handle LLM error responses
        if result.get("type") == "error":
            logger.warning(f"LLM qualification failed: {result.get('text')}. Falling back.")
            analysis = WebsiteAnalysis.objects.create(
                company=company,
                description="Analysis request failed.",
                lead_score=4.0,
                lead_score_reason=f"LLM router failed to respond. Error: {result.get('text')}"
            )
            return analysis

        raw_text = result.get("text", "")
        try:
            # Strip markdown JSON wrappers if present
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
            
            data = json.loads(raw_text)
            
            analysis = WebsiteAnalysis.objects.create(
                company=company,
                description=data.get("description", ""),
                has_delivery=bool(data.get("has_delivery")),
                has_scheduling=bool(data.get("has_scheduling")),
                needs_routing=bool(data.get("needs_routing")),
                fleet_size_estimate=data.get("fleet_size_estimate", "unknown"),
                lead_score=float(data.get("lead_score", 5.0)),
                lead_score_reason=data.get("lead_score_reason", "")
            )
            return analysis
        except Exception as e:
            logger.error(f"Failed to parse qualification JSON from LLM: {e}. Raw: {raw_text}")
            analysis = WebsiteAnalysis.objects.create(
                company=company,
                description="Failed to parse LLM analysis payload.",
                lead_score=5.0,
                lead_score_reason="Parsing error on model JSON payload."
            )
            return analysis
