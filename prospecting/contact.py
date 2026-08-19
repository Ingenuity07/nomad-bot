import logging
from prospecting.models import LeadCompany, LeadContact

logger = logging.getLogger(__name__)

class ContactExtractor:
    """Crawls business sites to extract emails, phone numbers, and social links using the Tool Platform."""

    @staticmethod
    def extract_contacts(company: LeadCompany, run_id: str = None) -> list:
        if not company.website:
            logger.info(f"No website resolved for company {company.name}. Skipping contact extraction.")
            return []

        discovered = []
        from llm.tools.registry import ToolRegistry
        from llm.tools.executor import ToolExecutor
        from llm.tools.context import ToolContext
        from chat.orchestrator.single_agent import SingleAgentOrchestrator

        orchestrator = SingleAgentOrchestrator()
        executor = ToolExecutor(orchestrator.tool_registry)
        correlated_run_id = run_id or (
            str(company.discovery_run_id) if company.discovery_run_id else None
        )
        context = ToolContext(run_id=correlated_run_id, source="workflow")

        logger.info(f"Crawling website via CrawlWebsiteTool: {company.website}")
        crawl_res = executor.execute(
            "crawl_website",
            {
                "url": company.website,
                "max_pages": 5,
                "timeout_seconds": 30
            },
            context=context
        )

        all_text = ""
        if crawl_res.success and crawl_res.data:
            for page in crawl_res.data.get("pages", []):
                all_text += page.get("text", "") + "\n"

        if not all_text.strip():
            return []

        logger.info("Parsing contact details via ExtractContactDataTool")
        extract_res = executor.execute(
            "extract_contact_data",
            {
                "text": all_text,
                "source_url": company.website
            },
            context=context
        )

        if extract_res.success and extract_res.data:
            data = extract_res.data
            
            # 1. Save unique emails
            for email in data.get("emails", []):
                # Filter DB duplicates
                contact, created = LeadContact.objects.get_or_create(
                    company=company,
                    email=email[:255],
                    defaults={"source": company.website[:2000]}
                )
                if created:
                    discovered.append(contact)

            # 2. Update company phone if not set
            for phone in data.get("phones", []):
                if not company.phone:
                    company.phone = phone[:50]
                    company.save()
                    break

            # 3. Save LinkedIn links
            for li_url in data.get("linkedin_urls", []):
                # Create a contact record for LinkedIn if not already created
                contact, created = LeadContact.objects.get_or_create(
                    company=company,
                    linkedin=li_url[:2000],
                    defaults={
                        "email": "linkedin@placeholder.com",
                        "role": "Company Page",
                        "source": company.website[:2000]
                    }
                )
                if created:
                    discovered.append(contact)

        return discovered
