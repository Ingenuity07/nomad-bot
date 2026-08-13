from typing import Optional, List
from django.db.models import QuerySet
from knowledge_base.models import UserProfile
from prospecting.models import DiscoveryRun, LeadCompany, LeadContact, WebsiteAnalysis

class DiscoveryRunRepository:
    @staticmethod
    def create_run(
        user_profile: UserProfile,
        keyword: str,
        location: str,
        status: str = 'pending'
    ) -> DiscoveryRun:
        return DiscoveryRun.objects.create(
            user_profile=user_profile,
            keyword=keyword,
            location=location,
            status=status
        )

    @staticmethod
    def get_run(run_id: str) -> Optional[DiscoveryRun]:
        try:
            return DiscoveryRun.objects.get(id=run_id)
        except DiscoveryRun.DoesNotExist:
            return None

    @staticmethod
    def update_status(
        run: DiscoveryRun,
        status: str,
        total_leads_found: int = 0
    ) -> DiscoveryRun:
        run.status = status
        if total_leads_found > 0:
            run.total_leads_found = total_leads_found
        run.save()
        return run


class LeadCompanyRepository:
    @staticmethod
    def create_company(
        discovery_run: DiscoveryRun,
        name: str,
        website: Optional[str] = None,
        phone: Optional[str] = None,
        address: Optional[str] = None,
        category: Optional[str] = None
    ) -> LeadCompany:
        return LeadCompany.objects.create(
            discovery_run=discovery_run,
            name=name[:255] if name else "Unknown",
            website=website[:2000] if website else None,
            phone=phone[:100] if phone else None,
            address=address,
            category=category[:100] if category else None
        )

    @staticmethod
    def get_company(company_id: str) -> Optional[LeadCompany]:
        try:
            return LeadCompany.objects.get(id=company_id)
        except LeadCompany.DoesNotExist:
            return None

    @staticmethod
    def filter_leads(
        score_min: Optional[float] = None,
        location: Optional[str] = None,
        category: Optional[str] = None
    ) -> QuerySet:
        queryset = LeadCompany.objects.all()
        if score_min is not None:
            queryset = queryset.filter(analysis__lead_score__gte=score_min)
        if location and location.strip():
            queryset = queryset.filter(address__icontains=location.strip())
        if category and category.strip():
            queryset = queryset.filter(category__iexact=category.strip())
        return queryset.order_by('-analysis__lead_score', 'name')


class LeadContactRepository:
    @staticmethod
    def create_contact(
        company: LeadCompany,
        email: str,
        phone: Optional[str] = None,
        linkedin: Optional[str] = None,
        role: Optional[str] = None,
        source: str = 'website'
    ) -> LeadContact:
        return LeadContact.objects.create(
            company=company,
            email=email[:255],
            phone=phone[:100] if phone else None,
            linkedin=linkedin[:2000] if linkedin else None,
            role=role[:100] if role else None,
            source=source[:2000]
        )


class WebsiteAnalysisRepository:
    @staticmethod
    def create_or_update_analysis(
        company: LeadCompany,
        lead_score: float,
        description: Optional[str] = None,
        has_delivery: bool = False,
        has_scheduling: bool = False,
        needs_routing: bool = False,
        fleet_size_estimate: str = 'unknown',
        lead_score_reason: Optional[str] = None
    ) -> WebsiteAnalysis:
        analysis, _ = WebsiteAnalysis.objects.update_or_create(
            company=company,
            defaults={
                'lead_score': lead_score,
                'description': description,
                'has_delivery': has_delivery,
                'has_scheduling': has_scheduling,
                'needs_routing': needs_routing,
                'fleet_size_estimate': fleet_size_estimate[:100],
                'lead_score_reason': lead_score_reason
            }
        )
        return analysis
