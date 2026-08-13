import json
import logging
from typing import Dict, Any, List
from llm.router import IntelligentRouter
from prospecting.models import LeadCompany, ProspectingCampaign, Person, ContactPoint, BuyingGroupMember

logger = logging.getLogger(__name__)
router = IntelligentRouter()

class BuyingGroupWorkflow:
    @staticmethod
    def run(company: LeadCompany, campaign: ProspectingCampaign, scraped_text: str = "") -> List[BuyingGroupMember]:
        """
        Runs the buying group discovery node. Scrapes contacts/team page data, 
        classifies roles, scores relevance, and persists results.
        """
        # If no page content was passed, fallback to matching values in WebsiteAnalysis description
        if not scraped_text:
            analysis = getattr(company, 'analysis', None)
            scraped_text = analysis.description if analysis else ""

        if not scraped_text:
            logger.warning(f"No text contents available to extract buying group for {company.name}")
            return []

        prompt = (
            f"Analyze this company website text excerpt and identify contact details or hiring team details:\n"
            f"'{scraped_text[:8000]}'\n\n"
            f"For any person discovered, classify their buying group role type relative to product category: '{campaign.product_description}'.\n"
            f"Allowed role types: DECISION_MAKER, PROBLEM_OWNER, CHAMPION, INFLUENCER, UNKNOWN.\n"
            f"Determine a relevance score (0-100) based on title fit and match reasoning."
        )

        schema = (
            "{"
            '  "people": ['
            "    {"
            '      "name": "string",'
            '      "first_name": "string",'
            '      "last_name": "string",'
            '      "title": "string",'
            '      "linkedin_url": "string (nullable)",'
            '      "role_type": "string (DECISION_MAKER/PROBLEM_OWNER/CHAMPION/INFLUENCER/UNKNOWN)",'
            '      "relevance_score": 80,'
            '      "reason": "string (evidence justifying role fit)",'
            '      "contact_points": ['
            "        {"
            '          "type": "string (EMAIL/PHONE/LINKEDIN)",'
            '          "value": "string"'
            "        }"
            "      ]"
            "    }"
            "  ]"
            "}"
        )

        system_prompt = "You are an expert lead enrichment agent. Return ONLY raw structured JSON matching the schema."
        full_prompt = f"{prompt}\n\nSchema:\n{schema}\n\nReturn ONLY raw JSON."
        
        result = router.generate(prompt=full_prompt, system_prompt=system_prompt)
        text = result.get("text", "").strip()

        # Clean markdown code block frames
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        created_members = []
        try:
            data = json.loads(text)
            for p_data in data.get("people", []):
                name = p_data.get("name")
                if not name:
                    continue

                # 1. Create or update Person
                person, _ = Person.objects.update_or_create(
                    company=company,
                    name=name,
                    defaults={
                        "first_name": p_data.get("first_name", ""),
                        "last_name": p_data.get("last_name", ""),
                        "title": p_data.get("title", ""),
                        "linkedin_url": p_data.get("linkedin_url", "")
                    }
                )

                # 2. Save Contact points
                for cp in p_data.get("contact_points", []):
                    cp_type = cp.get("type", "OTHER").upper()
                    if cp_type not in ["EMAIL", "PHONE", "LINKEDIN", "OTHER"]:
                        cp_type = "OTHER"
                        
                    ContactPoint.objects.get_or_create(
                        person=person,
                        type=cp_type,
                        value=cp.get("value")
                    )

                # 3. Save Buying Group Member link
                role = p_data.get("role_type", "UNKNOWN").upper()
                if role not in ["DECISION_MAKER", "PROBLEM_OWNER", "CHAMPION", "INFLUENCER", "UNKNOWN"]:
                    role = "UNKNOWN"

                member, _ = BuyingGroupMember.objects.update_or_create(
                    campaign=campaign,
                    company=company,
                    person=person,
                    defaults={
                        "role_type": role,
                        "relevance_score": p_data.get("relevance_score", 0),
                        "reason": p_data.get("reason", "Discovered in research run")
                    }
                )
                created_members.append(member)

        except Exception as e:
            logger.error(f"Failed parsing buying group JSON: {e}. Raw: {text}")

        return created_members
