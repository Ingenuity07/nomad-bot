import uuid
import logging
from abc import ABC, abstractmethod
from prospecting.models import LeadCompany, Person, CRMIntegrationRecord

logger = logging.getLogger(__name__)

class CRMProvider(ABC):
    @abstractmethod
    def upsert_company(self, company: LeadCompany) -> str:
        pass

    @abstractmethod
    def upsert_contact(self, person: Person) -> str:
        pass

    @abstractmethod
    def create_activity(self, company: LeadCompany, subject: str, activity_type: str) -> str:
        pass

    @abstractmethod
    def update_stage(self, company: LeadCompany, stage: str) -> bool:
        pass

    @abstractmethod
    def assign_owner(self, company: LeadCompany, owner_email: str) -> bool:
        pass


class MockCRMProvider(CRMProvider):
    def upsert_company(self, company: LeadCompany) -> str:
        """
        Sync company details to external CRM. Resolves duplicate checks.
        """
        record = CRMIntegrationRecord.objects.filter(
            company=company,
            external_crm='MockCRM'
        ).first()

        if record:
            logger.info(f"Company {company.name} already exists in CRM with ID {record.external_id}. Skipping sync.")
            return record.external_id

        # Simulates creating a new record externally
        external_id = f"crm-comp-{uuid.uuid4().hex[:12]}"
        CRMIntegrationRecord.objects.create(
            company=company,
            external_crm='MockCRM',
            external_id=external_id
        )
        logger.info(f"Synced company {company.name} to CRM. Assigned ID: {external_id}")
        return external_id

    def upsert_contact(self, person: Person) -> str:
        """
        Sync contact details to CRM.
        """
        record = CRMIntegrationRecord.objects.filter(
            person=person,
            external_crm='MockCRM'
        ).first()

        if record:
            logger.info(f"Contact {person.name} already exists in CRM with ID {record.external_id}.")
            return record.external_id

        external_id = f"crm-cont-{uuid.uuid4().hex[:12]}"
        CRMIntegrationRecord.objects.create(
            person=person,
            external_crm='MockCRM',
            external_id=external_id
        )
        logger.info(f"Synced contact {person.name} to CRM. Assigned ID: {external_id}")
        return external_id

    def create_activity(self, company: LeadCompany, subject: str, activity_type: str) -> str:
        activity_id = f"crm-act-{uuid.uuid4().hex[:12]}"
        logger.info(f"Created activity '{subject}' of type '{activity_type}' for {company.name} in CRM.")
        return activity_id

    def update_stage(self, company: LeadCompany, stage: str) -> bool:
        logger.info(f"Updated CRM stage for company {company.name} to: {stage}")
        return True

    def assign_owner(self, company: LeadCompany, owner_email: str) -> bool:
        logger.info(f"Assigned owner {owner_email} to company {company.name} in CRM.")
        return True
