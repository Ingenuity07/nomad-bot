import os
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from knowledge_base.models import UserProfile
from prospecting.models import DiscoveryRun, LeadCompany, LeadContact, WebsiteAnalysis
from prospecting.discovery import BusinessDiscoveryEngine
from prospecting.contact import ContactExtractor
from prospecting.analyzer import WebsiteAnalyzer

logger = logging.getLogger(__name__)

def get_default_user():
    user, _ = UserProfile.objects.get_or_create(
        username='default_user',
        defaults={'email': 'default@example.com', 'full_name': 'Shivam Singh'}
    )
    return user

class ProspectingDiscoverAPIView(APIView):
    """Trigger a new Lead Generation discovery and qualification run."""

    def post(self, request):
        user = get_default_user()
        keyword = request.data.get("keyword", "").strip()
        location = request.data.get("location", "").strip()

        if not keyword or not location:
            return Response({"error": "keyword and location are required"}, status=status.HTTP_400_BAD_REQUEST)

        # Create discovery run record
        run = DiscoveryRun.objects.create(
            user_profile=user,
            keyword=keyword,
            location=location,
            status='running'
        )

        try:
            # 1. Discover Businesses
            discovery_engine = BusinessDiscoveryEngine()
            companies = discovery_engine.discover_businesses(run)

            # Limit to top 5 results for synchronous execution safety (avoiding timeout)
            leads_to_process = LeadCompany.objects.filter(discovery_run=run)[:5]

            # 2. Extract Contacts & Analyze Websites
            analyzer = WebsiteAnalyzer()
            for company in leads_to_process:
                ContactExtractor.extract_contacts(company)
                analyzer.analyze_website(company)

            run.status = 'completed'
            run.total_leads_found = len(companies)
            run.save()

            return Response({
                "status": "success",
                "run_id": str(run.id),
                "leads_found": run.total_leads_found,
                "processed_leads_count": leads_to_process.count()
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception("Prospecting discovery run failed")
            run.status = 'failed'
            run.save()
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingLeadsAPIView(APIView):
    """Retrieve all leads in the CRM along with contacts and qualification scores, supporting filtering and pagination."""

    def get(self, request):
        # 1. Fetch query filters
        score_min = request.query_params.get("score_min")
        location = request.query_params.get("location")
        category = request.query_params.get("category")
        
        # 2. Fetch pagination params
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 10))
        except ValueError:
            page = 1
            page_size = 10

        # Build filter set
        queryset = LeadCompany.objects.all()
        
        if score_min:
            try:
                queryset = queryset.filter(analysis__lead_score__gte=float(score_min))
            except ValueError:
                pass
        if location and location.strip():
            queryset = queryset.filter(address__icontains=location.strip())
        if category and category.strip():
            queryset = queryset.filter(category__iexact=category.strip())
            
        queryset = queryset.order_by('-analysis__lead_score', 'name')
        
        total_count = queryset.count()
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        
        # Slice queryset for pagination
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_companies = queryset[start_idx:end_idx]

        leads = []
        for c in paginated_companies:
            contacts = [{
                "id": str(con.id),
                "email": con.email,
                "phone": con.phone,
                "linkedin": con.linkedin,
                "role": con.role
            } for con in c.contacts.all()]

            analysis_data = {}
            if hasattr(c, 'analysis'):
                analysis_data = {
                    "id": str(c.analysis.id),
                    "description": c.analysis.description,
                    "has_delivery": c.analysis.has_delivery,
                    "has_scheduling": c.analysis.has_scheduling,
                    "needs_routing": c.analysis.needs_routing,
                    "fleet_size_estimate": c.analysis.fleet_size_estimate,
                    "lead_score": int(c.analysis.lead_score * 10),  # Scale 1-10 to 10-100 percentage
                    "lead_score_reason": c.analysis.lead_score_reason
                }

            leads.append({
                "id": str(c.id),
                "name": c.name,
                "website": c.website,
                "phone": c.phone,
                "address": c.address,
                "category": c.category,
                "rating": c.rating,
                "contacts": contacts,
                "analysis": analysis_data,
                "created_at": c.created_at.isoformat()
            })

        # Dynamically fetch distinct list of non-empty categories in CRM for filter dropdowns
        unique_categories = list(LeadCompany.objects.values_list('category', flat=True).distinct())
        unique_categories = sorted(list(set([cat for cat in unique_categories if cat])))

        return Response({
            "leads": leads,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "categories": unique_categories
        }, status=status.HTTP_200_OK)


class ProspectingResetAPIView(APIView):
    """Clears all discovery runs and lead listings from the CRM."""

    def post(self, request):
        LeadContact.objects.all().delete()
        WebsiteAnalysis.objects.all().delete()
        LeadCompany.objects.all().delete()
        DiscoveryRun.objects.all().delete()
        return Response({"status": "reset_completed"}, status=status.HTTP_200_OK)
