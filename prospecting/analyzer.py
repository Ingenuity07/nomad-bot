import json
import logging
import requests
from bs4 import BeautifulSoup
from prospecting.models import LeadCompany, WebsiteAnalysis
from llm.router import IntelligentRouter

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

    def __init__(self, provider=None, trace_recorder=None):
        self.provider = provider or IntelligentRouter()
        self.trace = trace_recorder

    def analyze_website(self, company: LeadCompany) -> WebsiteAnalysis:
        if not company.website:
            logger.info(f"No website found for {company.name}. Creating generic low-score analysis.")
            if self.trace:
                self.trace.event(
                    "llm_scrape_interpretation",
                    f"No website analysis for {company.name}",
                    actor="workflow",
                    input_data={"company": company.name, "website": None},
                    output_data={"lead_score": 3.0, "reason": "No website resolved."},
                    status="error",
                    metadata={"parsed": False, "decision_source": "fallback; LLM skipped"},
                )
            analysis = WebsiteAnalysis.objects.create(
                company=company,
                description="No website resolved.",
                lead_score=3.0,
                lead_score_reason="Generic score assigned because no website was resolved for search verification."
            )
            return analysis

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        text_content = ""
        response_status = None
        scrape_error = None

        try:
            logger.info(f"Fetching website text for analysis: {company.website}")
            res = requests.get(company.website, headers=headers, timeout=8)
            response_status = res.status_code
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                # Remove script/style tags
                for s in soup(["script", "style", "meta", "noscript"]):
                    s.decompose()
                # Get clean text, trim spacing, limit size to fit LLM prompt context nicely
                text_content = " ".join(soup.get_text().split())[:8000]
        except Exception as e:
            logger.error(f"Failed to scrape homepage text for {company.name}: {e}")
            scrape_error = str(e)

        # Fallback if page download yielded no text
        scrape_source = "homepage"
        if not text_content.strip():
            text_content = f"Company Name: {company.name}. Category: {company.category or 'Business'}."
            scrape_source = "fallback_company_record"

        if self.trace:
            self.trace.event(
                "scraped_data",
                f"Homepage content prepared for {company.name}",
                actor="scraper:requests+beautifulsoup",
                input_data={"url": company.website, "timeout_seconds": 8},
                output_data={"cleaned_text": text_content},
                status="success" if scrape_source == "homepage" else "error",
                metadata={
                    "company_id": str(company.id),
                    "company_name": company.name,
                    "source": scrape_source,
                    "http_status": response_status,
                    "page_count": 1 if scrape_source == "homepage" else 0,
                    "character_count": len(text_content) if scrape_source == "homepage" else 0,
                    "error": scrape_error,
                },
            )

        prompt = (
            f"Company Name: {company.name}\n"
            f"Sector/Category: {company.category}\n\n"
            f"Website Scraped Content:\n{text_content}\n\n"
            "Analyze and output the structured JSON qualification:"
        )

        result = self.provider.generate(
            prompt=prompt,
            system_prompt=QUALIFICATION_SYSTEM_PROMPT,
            prompt_key="prospecting.web_qualifier.user",
            system_prompt_key="prospecting.web_qualifier.system",
            template_variables={
                "company_name": company.name,
                "category": company.category or "Business",
                "scraped_content": text_content
            }
        )

        # Handle LLM error responses
        if result.get("type") == "error":
            logger.warning(f"LLM qualification failed: {result.get('text')}. Falling back.")
            if self.trace:
                self.trace.event(
                    "llm_scrape_interpretation",
                    f"LLM qualification failed for {company.name}",
                    actor="llm:website-qualifier",
                    input_data={"system_prompt": QUALIFICATION_SYSTEM_PROMPT, "prompt": prompt},
                    output_data={"raw_response": result},
                    status="error",
                    metadata={"company_id": str(company.id), "parsed": False, "decision_source": "LLM"},
                )
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

            if self.trace:
                self.trace.event(
                    "llm_scrape_interpretation",
                    f"LLM interpreted scraped data for {company.name}",
                    actor="llm:website-qualifier",
                    input_data={"system_prompt": QUALIFICATION_SYSTEM_PROMPT, "prompt": prompt},
                    output_data={"raw_response": result, "parsed_analysis": data},
                    status="success",
                    metadata={"company_id": str(company.id), "parsed": True, "decision_source": "LLM"},
                )
            
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
            if self.trace:
                self.trace.event(
                    "llm_scrape_interpretation",
                    f"Could not parse LLM analysis for {company.name}",
                    actor="llm:website-qualifier",
                    input_data={"system_prompt": QUALIFICATION_SYSTEM_PROMPT, "prompt": prompt},
                    output_data={"raw_response": result, "parse_error": str(e)},
                    status="error",
                    metadata={"company_id": str(company.id), "parsed": False, "decision_source": "LLM"},
                )
            analysis = WebsiteAnalysis.objects.create(
                company=company,
                description="Failed to parse LLM analysis payload.",
                lead_score=5.0,
                lead_score_reason="Parsing error on model JSON payload."
            )
            return analysis
