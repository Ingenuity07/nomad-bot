import logging
from typing import Optional
from prospecting.models import LeadCompany
from prospecting.discovery.dto import DiscoveryResultItem
from prospecting.discovery.normalizer import Normalizer

logger = logging.getLogger(__name__)

class Deduplicator:
    @staticmethod
    def find_existing_company(item: DiscoveryResultItem) -> Optional[LeadCompany]:
        """
        Deduplicates companies by normalized website domain, phone number, or name.
        """
        # 1. Match by normalized website domain
        if item.website:
            normalized_domain = Normalizer.normalize_domain(item.website)
            if normalized_domain:
                # Find any existing company where website domain matches
                for company in LeadCompany.objects.filter(website__isnull=False):
                    if Normalizer.normalize_domain(company.website) == normalized_domain:
                        logger.info(f"Deduplicated by domain: '{item.name}' matched existing '{company.name}'")
                        return company

        # 2. Match by normalized phone number
        if item.phone:
            normalized_phone = Normalizer.normalize_phone(item.phone)
            if normalized_phone and len(normalized_phone) >= 7:
                for company in LeadCompany.objects.filter(phone__isnull=False):
                    comp_phone = Normalizer.normalize_phone(company.phone)
                    if comp_phone and len(comp_phone) >= 7:
                        if comp_phone[-7:] == normalized_phone[-7:]:
                            logger.info(f"Deduplicated by phone: '{item.name}' matched existing '{company.name}'")
                            return company

        # 3. Match by normalized name
        normalized_name = Normalizer.normalize_name(item.name)
        if normalized_name:
            for company in LeadCompany.objects.all():
                if Normalizer.normalize_name(company.name) == normalized_name:
                    logger.info(f"Deduplicated by name: '{item.name}' matched existing '{company.name}'")
                    return company

        return None
