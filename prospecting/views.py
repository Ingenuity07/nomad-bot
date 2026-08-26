import os
import logging
import uuid
from django.http import FileResponse
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from knowledge_base.models import UserProfile

from prospecting.models import (
    DiscoveryRun, LeadCompany, LeadContact, WebsiteAnalysis, ProblemSignal, ResearchRun,
    Evidence, CompanySignal, Person, ContactPoint, BuyingGroupMember,
    TargetList, ListMembership, CampaignEnrollment, SalesGuidance, ProspectingCampaign,
    EmailSequence, EmailMessage, EmailBounce, EmailUnsubscribe, InboundReply, LeadFeedback,
    get_default_workspace, CRMIntegrationRecord
)
from prospecting.serializers import (
    ProblemSignalSerializer, LeadCompanySerializer, EvidenceSerializer,
    CompanySignalSerializer, PersonSerializer, BuyingGroupMemberSerializer,
    TargetListSerializer, CampaignEnrollmentSerializer, SalesGuidanceSerializer,
    EmailSequenceSerializer, EmailMessageSerializer, InboundReplySerializer, LeadFeedbackSerializer,
    CRMIntegrationRecordSerializer, ProspectingCampaignSerializer,
    DiscoveryRunSerializer
)
from prospecting.discovery.engine import BusinessDiscoveryEngine
from prospecting.contact import ContactExtractor
from prospecting.analyzer import WebsiteAnalyzer
from llm.router import IntelligentRouter
from prospecting.discovery.tracing import (
    DiscoveryTraceRecorder,
    discovery_trace_paths,
    load_discovery_trace,
)

logger = logging.getLogger(__name__)
router = IntelligentRouter()

def get_default_user():
    user, _ = UserProfile.objects.get_or_create(
        username='default_user',
        defaults={'email': 'default@example.com', 'full_name': 'Shivam Singh'}
    )
    return user

from prospecting.tasks import discover_campaign_async
from prospecting.campaigns import ensure_campaign_for_run

class ProspectingDiscoverAPIView(APIView):
    """Trigger a new Lead Generation discovery and qualification run."""

    def post(self, request):
        user = get_default_user()
        keyword = request.data.get("keyword", "").strip()
        location = request.data.get("location", "").strip()

        if not keyword or not location:
            return Response({"error": "keyword and location are required"}, status=status.HTTP_400_BAD_REQUEST)

        # Create discovery run record in pending state
        run = DiscoveryRun.objects.create(
            user_profile=user,
            keyword=keyword,
            location=location,
            status='pending'
        )
        trace_recorder = DiscoveryTraceRecorder(str(run.id))
        trace_recorder.initialize({
            "keyword": keyword,
            "location": location,
            "request_payload": dict(request.data),
        })
        trace_url = f"/api/v3/prospecting/discovery-runs/{run.id}/trace/"
        ensure_campaign_for_run(run, user=user)

        try:
            is_dev = os.environ.get("DEV", "False").lower() in ("true", "1", "yes")
            if is_dev:
                # Process synchronously
                result = discover_campaign_async(str(run.id))
                return Response({
                    "status": "success",
                    "run_id": str(run.id),
                    "trace_url": trace_url,
                    "message": "Discovery run executed synchronously.",
                    "result": result
                }, status=status.HTTP_200_OK)
            else:
                # Dispatch asynchronous celery task
                discover_campaign_async.delay(str(run.id))
                return Response({
                    "status": "success",
                    "run_id": str(run.id),
                    "trace_url": trace_url,
                    "message": "Discovery run queued successfully."
                }, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception("Failed to execute or dispatch prospecting task")
            run.status = 'failed'
            run.save()
            trace_recorder.event(
                "error",
                "Discovery dispatch failed",
                actor="api",
                output_data={"error": str(e), "exception_type": type(e).__name__},
                status="error",
            )
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingLeadsAPIView(APIView):
    """Retrieve all leads in the CRM along with contacts and qualification scores, supporting filtering and pagination."""

    def get(self, request):
        # 1. Fetch query filters
        score_min = request.query_params.get("score_min")
        location = request.query_params.get("location")
        category = request.query_params.get("category")
        campaign_id = getattr(request, 'campaign_id', None) or request.query_params.get("campaign_id")
        run_id = getattr(request, 'run_id', None) or request.query_params.get("run_id")
        
        # 2. Fetch pagination params
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 10))
        except ValueError:
            page = 1
            page_size = 10

        # Build filter set
        workspace = get_default_workspace()
        queryset = LeadCompany.objects.select_related(
            'analysis', 'campaign', 'discovery_run__campaign'
        ).prefetch_related(
            'contacts', 'campaign_insights', 'research_runs'
        ).filter(
            Q(campaign__workspace=workspace) |
            Q(discovery_run__campaign__workspace=workspace) |
            Q(discovery_leads__discovery_run__campaign__workspace=workspace) |
            Q(campaign__isnull=True, discovery_run__campaign__isnull=True)
        ).distinct()
        
        if score_min:
            try:
                minimum_score = max(0.0, min(float(score_min), 100.0))
                insight_filter = Q(campaign_insights__fit_score__gte=minimum_score)
                if campaign_id:
                    insight_filter &= Q(campaign_insights__campaign_id=campaign_id)
                queryset = queryset.filter(
                    insight_filter |
                    Q(analysis__lead_score__gte=minimum_score / 10)
                ).distinct()
            except ValueError:
                pass
        if location and location.strip():
            queryset = queryset.filter(address__icontains=location.strip())
        if category and category.strip():
            queryset = queryset.filter(category__iexact=category.strip())
        if campaign_id and campaign_id.strip():
            queryset = queryset.filter(
                Q(campaign_id=campaign_id.strip()) |
                Q(discovery_run__campaign_id=campaign_id.strip()) |
                Q(discovery_leads__discovery_run__campaign_id=campaign_id.strip())
            ).distinct()
        if run_id and run_id.strip():
            queryset = queryset.filter(
                Q(discovery_run_id=run_id.strip()) |
                Q(discovery_leads__discovery_run_id=run_id.strip())
            ).distinct()
            
        queryset = queryset.order_by('-created_at', 'name')
        
        total_count = queryset.count()
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        
        # Slice queryset for pagination
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_companies = queryset[start_idx:end_idx]

        leads = []
        for c in paginated_companies:
            raw_contacts = [{
                "id": str(con.id),
                "email": con.email,
                "phone": con.phone,
                "linkedin": con.linkedin,
                "role": con.role
            } for con in c.contacts.all()]

            insight = None
            if campaign_id:
                insight = next(
                    (item for item in c.campaign_insights.all() if str(item.campaign_id) == str(campaign_id)),
                    None,
                )
            if insight is None:
                insight = next(iter(c.campaign_insights.all()), None)

            analysis_data = {}
            if insight is not None:
                analysis_data = {
                    "id": str(insight.id),
                    "description": insight.company_summary,
                    "has_delivery": bool(insight.operational_profile.get('has_delivery')),
                    "has_scheduling": bool(insight.operational_profile.get('has_scheduling')),
                    "needs_routing": bool(insight.operational_profile.get('needs_routing')),
                    "fleet_size_estimate": insight.operational_profile.get('fleet_size_estimate', 'unknown'),
                    "lead_score": float(insight.fit_score) if insight.fit_score is not None else None,
                    "lead_score_reason": insight.fit_reason,
                    "fit_level": insight.fit_level,
                    "confidence": float(insight.confidence),
                    "data_gaps": insight.data_gaps,
                }
            elif hasattr(c, 'analysis'):
                analysis_data = {
                    "id": str(c.analysis.id),
                    "description": c.analysis.description,
                    "has_delivery": c.analysis.has_delivery,
                    "has_scheduling": c.analysis.has_scheduling,
                    "needs_routing": c.analysis.needs_routing,
                    "fleet_size_estimate": c.analysis.fleet_size_estimate,
                    "lead_score": min(100, round(float(c.analysis.lead_score) * 10, 1)),
                    "lead_score_reason": c.analysis.lead_score_reason
                }

            campaign_research = [
                item for item in c.research_runs.all()
                if not campaign_id or str(item.campaign_id) in {str(campaign_id), 'None'}
            ]
            latest_research = max(campaign_research, key=lambda item: item.created_at, default=None)
            has_analysis = bool(analysis_data)
            if latest_research and latest_research.status in {'QUEUED', 'RUNNING'}:
                research_status = latest_research.status
            elif has_analysis:
                research_status = 'COMPLETED'
            elif latest_research:
                research_status = latest_research.status
            else:
                research_status = 'NOT_STARTED'
            data_locked = not has_analysis

            leads.append({
                "id": str(c.id),
                "name": c.name,
                "website": c.website,
                "phone": None if data_locked else c.phone,
                "address": c.address,
                "category": c.category,
                "rating": c.rating,
                "contacts": [] if data_locked else raw_contacts,
                "analysis": {} if data_locked else analysis_data,
                "data_locked": data_locked,
                "research_status": research_status,
                "research_run_id": str(latest_research.id) if latest_research else None,
                "website_available": bool(c.website),
                "created_at": c.created_at.isoformat()
            })

        # Dynamically fetch distinct list of non-empty categories in CRM for filter dropdowns
        unique_categories = list(queryset.values_list('category', flat=True).distinct())
        unique_categories = sorted(list(set([cat for cat in unique_categories if cat])))

        return Response({
            "leads": leads,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "categories": unique_categories
        }, status=status.HTTP_200_OK)


class ProspectingCampaignListAPIView(APIView):
    """List campaigns in the current workspace."""

    def get(self, request):
        campaigns = (
            ProspectingCampaign.objects
            .filter(workspace=get_default_workspace())
            .prefetch_related('companies', 'discovery_runs')
            .order_by('-created_at')
        )
        serializer = ProspectingCampaignSerializer(campaigns, many=True)
        return Response({"campaigns": serializer.data}, status=status.HTTP_200_OK)


class ProspectingCampaignDetailAPIView(APIView):
    """Retrieve one campaign in the current workspace."""

    def get(self, request, pk):
        try:
            campaign = (
                ProspectingCampaign.objects
                .prefetch_related('companies', 'discovery_runs')
                .get(id=pk, workspace=get_default_workspace())
            )
        except ProspectingCampaign.DoesNotExist:
            return Response({"error": "Campaign not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            ProspectingCampaignSerializer(campaign).data,
            status=status.HTTP_200_OK
        )


class ProspectingCampaignLeadsAPIView(ProspectingLeadsAPIView):
    """List leads belonging to one campaign, with standard lead filters and pagination."""

    def get(self, request, pk):
        if not ProspectingCampaign.objects.filter(
            id=pk,
            workspace=get_default_workspace()
        ).exists():
            return Response({"error": "Campaign not found"}, status=status.HTTP_404_NOT_FOUND)

        request.campaign_id = str(pk)
        return super().get(request)


def _visible_discovery_runs():
    workspace = get_default_workspace()
    user = get_default_user()
    return DiscoveryRun.objects.filter(
        Q(campaign__workspace=workspace) |
        Q(campaign__isnull=True, user_profile=user)
    ).select_related(
        'campaign', 'prospecting_request', 'specification_version'
    ).prefetch_related('companies').distinct()


class DiscoveryRunListAPIView(APIView):
    """List search executions with campaign metadata and lead metrics."""

    def get(self, request):
        queryset = _visible_discovery_runs()
        run_status = request.query_params.get('status')
        search = request.query_params.get('search')
        campaign_id = request.query_params.get('campaign_id')

        if run_status and run_status.strip():
            queryset = queryset.filter(status__iexact=run_status.strip())
        if search and search.strip():
            queryset = queryset.filter(
                Q(keyword__icontains=search.strip()) |
                Q(location__icontains=search.strip()) |
                Q(campaign__name__icontains=search.strip())
            )
        if campaign_id and campaign_id.strip():
            queryset = queryset.filter(campaign_id=campaign_id.strip())

        try:
            page = max(1, int(request.query_params.get('page', 1)))
            page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))
        except ValueError:
            page, page_size = 1, 20

        queryset = queryset.order_by('-started_at')
        total_count = queryset.count()
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        start = (page - 1) * page_size
        runs = queryset[start:start + page_size]

        return Response({
            'discovery_runs': DiscoveryRunSerializer(runs, many=True).data,
            'total_count': total_count,
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages,
        }, status=status.HTTP_200_OK)


class DiscoveryRunDetailAPIView(APIView):
    """Retrieve one search execution with campaign and lead metrics."""

    def get(self, request, pk):
        try:
            run = _visible_discovery_runs().get(id=pk)
        except DiscoveryRun.DoesNotExist:
            return Response(
                {'error': 'Discovery run not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(DiscoveryRunSerializer(run).data, status=status.HTTP_200_OK)


class DiscoveryRunTraceAPIView(APIView):
    """Open the self-contained execution viewer or retrieve its raw JSON."""

    def get(self, request, pk):
        try:
            run = _visible_discovery_runs().get(id=pk)
        except DiscoveryRun.DoesNotExist:
            return Response(
                {'error': 'Discovery run not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        trace = load_discovery_trace(str(pk))
        if trace is None:
            DiscoveryTraceRecorder(str(pk)).initialize({
                "keyword": run.keyword,
                "location": run.location,
                "legacy_trace": True,
            })
            trace = load_discovery_trace(str(pk))

        if request.query_params.get("raw", "").lower() in ("1", "true", "yes"):
            return Response(trace, status=status.HTTP_200_OK)

        if request.query_params.get("download", "").lower() in ("md", "markdown", "flow"):
            flow_path = discovery_trace_paths(str(pk))["flow"]
            if not flow_path.exists():
                return Response(
                    {'error': 'Discovery flow file is not available'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return FileResponse(
                flow_path.open("rb"),
                as_attachment=True,
                content_type="text/markdown; charset=utf-8",
                filename=f"discovery-{pk}-flow.md",
            )

        html_path = discovery_trace_paths(str(pk))["html"]
        if not html_path.exists():
            return Response(
                {'error': 'Discovery trace viewer is not available'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return FileResponse(
            html_path.open("rb"),
            content_type="text/html; charset=utf-8",
            filename=f"discovery-{pk}-trace.html",
        )


class DiscoveryRunLeadsAPIView(ProspectingLeadsAPIView):
    """List leads found in one search execution."""

    def get(self, request, pk):
        run = _visible_discovery_runs().filter(id=pk).first()
        if run is None:
            return Response(
                {'error': 'Discovery run not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        request.run_id = str(pk)
        if run.campaign_id:
            request.campaign_id = str(run.campaign_id)
        return super().get(request)


class DiscoveryRunLeadResearchAPIView(APIView):
    """Queue website research for one to five user-selected leads in a run."""

    def post(self, request, pk):
        run = _visible_discovery_runs().filter(id=pk).first()
        if run is None:
            return Response(
                {'error': 'Discovery run not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        lead_ids = request.data.get('lead_ids')
        if not isinstance(lead_ids, list):
            return Response(
                {'error': 'lead_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lead_ids = list(dict.fromkeys(str(value) for value in lead_ids if value))
        if not lead_ids or len(lead_ids) > 5:
            return Response(
                {'error': 'Select between 1 and 5 leads per research request'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        companies = LeadCompany.objects.filter(
            Q(discovery_run=run) | Q(discovery_leads__discovery_run=run),
            id__in=lead_ids,
        ).distinct()
        company_map = {str(company.id): company for company in companies}
        missing = [lead_id for lead_id in lead_ids if lead_id not in company_map]
        if missing:
            return Response(
                {'error': 'One or more selected leads do not belong to this run', 'lead_ids': missing},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queued = []
        errors = []
        for lead_id in lead_ids:
            company = company_map[lead_id]
            if not company.website:
                errors.append({'lead_id': lead_id, 'error': 'No website available'})
                continue
            research_run, created = queue_lead_research(company, run.campaign, run)
            queued.append({
                'lead_id': lead_id,
                'research_run_id': str(research_run.id),
                'status': research_run.status,
                'created': created,
            })

        response_status = status.HTTP_202_ACCEPTED if queued else status.HTTP_400_BAD_REQUEST
        return Response({'queued': queued, 'errors': errors}, status=response_status)


class ProspectingResetAPIView(APIView):
    """Clears all discovery runs and lead listings from the CRM."""

    def post(self, request):
        LeadContact.objects.all().delete()
        WebsiteAnalysis.objects.all().delete()
        LeadCompany.objects.all().delete()
        DiscoveryRun.objects.all().delete()
        return Response({"status": "reset_completed"}, status=status.HTTP_200_OK)


class ProblemSignalListCreateAPIView(APIView):
    """List and create problem signals."""
    def get(self, request):
        signals = ProblemSignal.objects.all()
        active_filter = request.query_params.get("active")
        if active_filter is not None:
            active_bool = active_filter.lower() == 'true'
            signals = signals.filter(active=active_bool)
        
        serializer = ProblemSignalSerializer(signals, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ProblemSignalSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProblemSignalDetailAPIView(APIView):
    """Retrieve, update, or deactivate specific signals."""
    def get(self, request, pk):
        try:
            signal = ProblemSignal.objects.get(id=pk)
            serializer = ProblemSignalSerializer(signal)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except ProblemSignal.DoesNotExist:
            return Response({"error": "Signal not found"}, status=status.HTTP_404_NOT_FOUND)

    def patch(self, request, pk):
        try:
            signal = ProblemSignal.objects.get(id=pk)
            serializer = ProblemSignalSerializer(signal, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except ProblemSignal.DoesNotExist:
            return Response({"error": "Signal not found"}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, pk):
        try:
            signal = ProblemSignal.objects.get(id=pk)
            # Soft delete: toggle active state to preserve historical evidence reference integrity
            signal.active = False
            signal.save()
            return Response({"status": "deactivated"}, status=status.HTTP_200_OK)
        except ProblemSignal.DoesNotExist:
            return Response({"error": "Signal not found"}, status=status.HTTP_404_NOT_FOUND)


class LeadDetailAPIView(APIView):
    """Retrieve and patch lead company details."""
    def get(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            serializer = LeadCompanySerializer(company)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)

    def patch(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            serializer = LeadCompanySerializer(company, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)


class LeadEvidenceAPIView(APIView):
    """Get list of evidence records for a lead."""
    def get(self, request, pk):
        evidence = Evidence.objects.filter(company_id=pk)
        serializer = EvidenceSerializer(evidence, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LeadSignalsAPIView(APIView):
    """Get list of signals for a lead."""
    def get(self, request, pk):
        signals = CompanySignal.objects.filter(company_id=pk)
        serializer = CompanySignalSerializer(signals, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LeadContactsAPIView(APIView):
    """Get list of contacts (Person + ContactPoint) for a lead."""
    def get(self, request, pk):
        people = Person.objects.filter(company_id=pk)
        serializer = PersonSerializer(people, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LeadBuyingGroupAPIView(APIView):
    """Get list of buying group members for a lead."""
    def get(self, request, pk):
        members = BuyingGroupMember.objects.filter(company_id=pk)
        serializer = BuyingGroupMemberSerializer(members, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


def queue_lead_research(company, campaign=None, discovery_run=None):
    """Create one durable research run, reusing an active request when present."""
    active = company.research_runs.filter(
        campaign=campaign,
        status__in=['QUEUED', 'RUNNING'],
    ).order_by('-created_at').first()
    if active:
        return active, False

    research_run = ResearchRun.objects.create(
        company=company,
        campaign=campaign,
        status='QUEUED',
    )
    try:
        from prospecting.tasks import research_lead_async
        research_lead_async.delay(
            str(research_run.id),
            str(discovery_run.id) if discovery_run else None,
        )
    except Exception as exc:
        research_run.status = 'FAILED'
        research_run.error = {'message': str(exc), 'exception_type': type(exc).__name__}
        research_run.save(update_fields=['status', 'error'])
        raise
    return research_run, True


class LeadResearchAPIView(APIView):
    """Queue website scraping and LLM analysis for one user-selected lead."""

    def post(self, request, pk):
        try:
            company = LeadCompany.objects.select_related(
                'campaign', 'discovery_run__campaign'
            ).get(id=pk)
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)

        if not company.website:
            return Response({"error": "Lead has no website to research"}, status=status.HTTP_400_BAD_REQUEST)

        campaign_id = request.data.get('campaign_id') or request.query_params.get('campaign_id')
        campaign = None
        if campaign_id:
            campaign = ProspectingCampaign.objects.filter(
                id=campaign_id,
                workspace=get_default_workspace(),
            ).first()
            if campaign is None:
                return Response({"error": "Campaign not found"}, status=status.HTTP_404_NOT_FOUND)
        elif company.campaign_id:
            campaign = company.campaign
        elif company.discovery_run_id:
            campaign = company.discovery_run.campaign

        research_run, created = queue_lead_research(company, campaign, company.discovery_run)
        return Response({
            "status": research_run.status,
            "research_run_id": str(research_run.id),
            "created": created,
        }, status=status.HTTP_202_ACCEPTED)


class LeadRefreshAPIView(APIView):
    """Triggers business discovery/enrichment task again."""
    def post(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            from prospecting.workflows.research_graph import website_research_graph
            res = website_research_graph.invoke({
                "company_id": str(company.id),
                "campaign_id": str(company.campaign.id) if company.campaign else None,
                "research_goal": "Refresh account intelligence indicators."
            })
            if company.campaign:
                from prospecting.qualification.scoring import OverallQualificationScorer
                OverallQualificationScorer.run_scoring(company, company.campaign)
            return Response({"status": "refresh_completed"}, status=status.HTTP_200_OK)
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)


class LeadIntelligenceAPIView(APIView):
    """Return one efficient, campaign-aware payload for the lead detail page."""

    @staticmethod
    def _normalise_score(value):
        """Older website analyses use 0-10; campaign insights use 0-100."""
        if value is None:
            return None
        score = float(value)
        if score <= 10:
            score *= 10
        return round(max(0.0, min(score, 100.0)), 1)

    @staticmethod
    def _campaign_for(company, requested_campaign_id):
        membership = Q(companies=company) | Q(discovery_runs__companies=company) | Q(
            discovery_runs__discovery_leads__company=company
        )
        visible = ProspectingCampaign.objects.filter(
            workspace=get_default_workspace()
        ).filter(membership).distinct()

        if requested_campaign_id:
            try:
                uuid.UUID(str(requested_campaign_id))
            except (ValueError, TypeError, AttributeError):
                return None
            return visible.filter(id=requested_campaign_id).first()
        if company.campaign_id:
            return visible.filter(id=company.campaign_id).first()
        if company.discovery_run_id and company.discovery_run.campaign_id:
            return visible.filter(id=company.discovery_run.campaign_id).first()
        return visible.order_by('-created_at').first()

    @staticmethod
    def _contacts(company):
        """Merge normalized people and legacy crawl contacts without losing emails."""
        people = list(company.people.all())
        contacts = list(PersonSerializer(people, many=True).data)
        seen_emails = {
            point['value'].strip().lower()
            for contact in contacts
            for point in contact.get('contact_points', [])
            if point.get('type') == 'EMAIL' and point.get('value')
        }
        seen_linkedin = {
            str(contact.get('linkedin_url', '')).strip().lower()
            for contact in contacts
            if contact.get('linkedin_url')
        }

        for legacy in company.contacts.all():
            email = (legacy.email or '').strip()
            linkedin = (legacy.linkedin or '').strip()
            is_placeholder = email.endswith('@placeholder.com')
            if (not email or is_placeholder or email.lower() in seen_emails) and (
                not linkedin or linkedin.lower() in seen_linkedin
            ):
                continue
            points = []
            if email and not is_placeholder and email.lower() not in seen_emails:
                points.append({
                    'id': f'legacy-email-{legacy.id}',
                    'type': 'EMAIL',
                    'value': email,
                    'source': legacy.source,
                    'verification_status': 'UNKNOWN',
                    'confidence': 1.0,
                })
                seen_emails.add(email.lower())
            if legacy.phone:
                points.append({
                    'id': f'legacy-phone-{legacy.id}',
                    'type': 'PHONE',
                    'value': legacy.phone,
                    'source': legacy.source,
                    'verification_status': 'UNKNOWN',
                    'confidence': 1.0,
                })
            if linkedin and linkedin.lower() not in seen_linkedin:
                points.append({
                    'id': f'legacy-linkedin-{legacy.id}',
                    'type': 'LINKEDIN',
                    'value': linkedin,
                    'source': legacy.source,
                    'verification_status': 'UNKNOWN',
                    'confidence': 1.0,
                })
                seen_linkedin.add(linkedin.lower())
            contacts.append({
                'id': str(legacy.id),
                'name': legacy.name or 'Company contact',
                'first_name': None,
                'last_name': None,
                'title': legacy.role or 'Contact found on company website',
                'linkedin_url': linkedin or None,
                'contact_points': points,
            })
        return contacts

    def get(self, request, pk):
        try:
            company = LeadCompany.objects.select_related(
                'analysis', 'campaign', 'discovery_run__campaign'
            ).prefetch_related(
                'contacts', 'people__contact_points', 'campaign_insights',
                'qualifications', 'evidence_records__signal', 'signals__signal',
                'buying_group_members__person__contact_points', 'guidance_records',
                'research_runs',
            ).get(id=pk)

            requested_campaign_id = request.query_params.get('campaign_id')
            campaign = self._campaign_for(company, requested_campaign_id)
            if requested_campaign_id and campaign is None:
                return Response(
                    {"error": "Lead was not found in the requested campaign"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            insight = None
            if campaign:
                insight = next(
                    (item for item in company.campaign_insights.all() if item.campaign_id == campaign.id),
                    None,
                )
            if insight is None and not requested_campaign_id:
                insight = next(iter(company.campaign_insights.all()), None)

            qualifications = list(company.qualifications.all())
            if campaign:
                qualifications = [item for item in qualifications if item.campaign_id == campaign.id]
            latest_qual = max(qualifications, key=lambda item: item.analysis_version, default=None)
            analysis = getattr(company, 'analysis', None)
            company_data = LeadCompanySerializer(company).data
            relevant_research_runs = [
                item for item in company.research_runs.all()
                if campaign is None or item.campaign_id in {None, campaign.id}
            ]
            latest_research_attempt = max(
                relevant_research_runs,
                key=lambda item: item.created_at,
                default=None,
            )
            has_analysis = bool(insight or analysis)
            if latest_research_attempt and latest_research_attempt.status in {'QUEUED', 'RUNNING'}:
                research_status = latest_research_attempt.status
            elif has_analysis:
                research_status = 'COMPLETED'
            elif latest_research_attempt:
                research_status = latest_research_attempt.status
            else:
                research_status = 'NOT_STARTED'

            if not has_analysis:
                company_data['phone'] = None
                return Response({
                    "company": company_data,
                    "campaign": {
                        "id": str(campaign.id),
                        "name": campaign.name,
                        "product_description": campaign.product_description,
                        "problem_statement": campaign.problem_statement,
                    } if campaign else None,
                    "data_locked": True,
                    "research_status": research_status,
                    "research_run_id": str(latest_research_attempt.id) if latest_research_attempt else None,
                    "analysis": {},
                    "problem_hypothesis": campaign.problem_statement if campaign else "",
                    "scores": {"problem_fit": None, "evidence_strength": None, "buying_window": None, "overall": None},
                    "explanation": {"overall_classification": "LOCKED", "positive_factors": [], "negative_factors": [], "unknowns": []},
                    "signals": [],
                    "evidence_timeline": [],
                    "source_summary": {"verifiable_sources": 0, "evidence_records": 0},
                    "contacts": [],
                    "buying_group": [],
                    "recommended_action": "Select Get lead data to research this account",
                    "talking_points": [],
                    "freshness": {"last_researched": None, "is_fresh": False},
                }, status=status.HTTP_200_OK)

            # Build components
            generic_categories = {
                "", "web search", "service", "point_of_interest", "establishment",
                "business", "company", "corporate_office",
            }
            category = str(company.category or "").strip()
            if category.lower() in generic_categories:
                run = company.discovery_run
                specification = run.specification_version.specification_json if run and run.specification_version else {}
                target = specification.get("target", {}) if isinstance(specification, dict) else {}
                category_candidates = []
                for field in ("industries", "categories"):
                    field_value = target.get(field, {})
                    values = field_value.get("value", []) if isinstance(field_value, dict) else field_value
                    if isinstance(values, list):
                        category_candidates.extend(str(value).strip() for value in values if str(value).strip())
                if category_candidates:
                    category = category_candidates[0]
                elif run and len(run.keyword.split()) <= 6:
                    category = run.keyword.strip().rstrip('.')
                else:
                    category = ""
            if insight and insight.industry:
                category = insight.industry
            cleaned_category = category.replace("_", " ")
            company_data["category"] = (
                cleaned_category if cleaned_category.isupper() else cleaned_category.title()
            ) if cleaned_category else None
            problem_hypothesis = (
                insight.fit_reason if insight else ""
            ) or (
                campaign.problem_statement if campaign else ""
            ) or (analysis.lead_score_reason if analysis else "") or ""

            evidence_records = list(company.evidence_records.all())
            if campaign:
                evidence_records = [
                    item for item in evidence_records
                    if item.campaign_id in {None, campaign.id}
                ]
            evidence_records.sort(key=lambda item: item.captured_at, reverse=True)
            signal_records = sorted(
                company.signals.all(), key=lambda item: item.last_detected_at, reverse=True
            )
            evidence_count = len(evidence_records)
            evidence_score = None
            if evidence_count:
                confidences = [float(item.confidence) for item in evidence_records]
                unique_sources = len({item.source_url for item in evidence_records})
                evidence_score = round(min(
                    (sum(confidences) / evidence_count * 50) + min(unique_sources * 20, 50),
                    100,
                ), 1)

            insight_score = float(insight.fit_score) if insight and insight.fit_score is not None else None
            analysis_score = self._normalise_score(analysis.lead_score) if analysis and insight is None else None
            scores = {
                "problem_fit": insight_score if insight_score is not None else (
                    round(float(latest_qual.problem_fit_score), 1) if latest_qual else analysis_score
                ),
                "evidence_strength": round(float(latest_qual.evidence_strength_score), 1) if latest_qual else evidence_score,
                "buying_window": round(float(latest_qual.buying_window_score), 1) if latest_qual else None,
                "overall": insight_score if insight_score is not None else (
                    round(float(latest_qual.overall_score), 1) if latest_qual else analysis_score
                ),
            }

            explanation = {
                "overall_classification": insight.fit_level if insight else (
                    latest_qual.explanation.get("overall_classification", "UNKNOWN") if latest_qual else "UNKNOWN"
                ),
                "positive_factors": insight.positive_factors if insight else (latest_qual.positive_factors if latest_qual else []),
                "negative_factors": insight.negative_factors if insight else (latest_qual.negative_factors if latest_qual else []),
                "unknowns": insight.data_gaps if insight else (latest_qual.unknowns if latest_qual else []),
            }

            signals = CompanySignalSerializer(signal_records, many=True).data
            evidence = EvidenceSerializer(evidence_records, many=True).data
            contacts = self._contacts(company)
            company_data["emails"] = sorted({
                point["value"]
                for contact in contacts
                for point in contact.get("contact_points", [])
                if point.get("type") == "EMAIL" and point.get("value")
            })
            buying_group_records = list(company.buying_group_members.all())
            if campaign:
                buying_group_records = [item for item in buying_group_records if item.campaign_id == campaign.id]
            buying_group = BuyingGroupMemberSerializer(buying_group_records, many=True).data

            guidance_records = list(company.guidance_records.all())
            if campaign:
                guidance_records = [item for item in guidance_records if item.campaign_id == campaign.id]
            guidance = max(guidance_records, key=lambda item: item.created_at, default=None)

            overall_score = scores["overall"]
            if guidance:
                recommended_action = guidance.recommended_next_step
            elif insight and insight.recommended_next_step:
                recommended_action = insight.recommended_next_step
            elif overall_score is None:
                recommended_action = "Research this account before outreach"
            elif overall_score >= 75:
                recommended_action = "Contact a relevant decision maker"
            elif overall_score >= 50:
                recommended_action = "Verify the strongest signal"
            else:
                recommended_action = "Keep monitoring for a stronger signal"

            research_runs = [
                item for item in company.research_runs.all()
                if item.status == 'COMPLETED' and item.completed_at and (
                    campaign is None or item.campaign_id in {None, campaign.id}
                )
            ]
            latest_research = max(research_runs, key=lambda item: item.completed_at, default=None)
            latest_evidence = evidence_records[0] if evidence_records else None
            last_researched = (
                latest_research.completed_at if latest_research else
                latest_evidence.captured_at if latest_evidence else
                insight.analyzed_at if insight else
                analysis.created_at if analysis else None
            )
            from django.utils import timezone
            freshness = {
                "last_researched": last_researched.isoformat() if last_researched else None,
                "is_fresh": bool(last_researched and (timezone.now() - last_researched).days <= 30)
            }

            analysis_payload = {
                "schema_version": insight.schema_version if insight else None,
                "summary": insight.company_summary if insight else (analysis.description if analysis else ""),
                "industry": insight.industry if insight else "",
                "business_model": insight.business_model if insight else "",
                "services": insight.services if insight else [],
                "operational_profile": insight.operational_profile if insight else {
                    "has_delivery": bool(analysis and analysis.has_delivery),
                    "has_scheduling": bool(analysis and analysis.has_scheduling),
                    "needs_routing": bool(analysis and analysis.needs_routing),
                    "fleet_size_estimate": analysis.fleet_size_estimate if analysis else "unknown",
                },
                "fit_score": insight_score if insight_score is not None else analysis_score,
                "fit_level": insight.fit_level if insight else "UNKNOWN",
                "fit_reason": problem_hypothesis,
                "confidence": float(insight.confidence) if insight else None,
                "positive_factors": explanation["positive_factors"],
                "negative_factors": explanation["negative_factors"],
                "data_gaps": explanation["unknowns"],
                "recommended_next_step": recommended_action,
                "talking_points": (
                    guidance.talking_points if guidance else
                    insight.talking_points if insight else
                    explanation["positive_factors"][:3]
                ),
                "analyzed_at": insight.analyzed_at.isoformat() if insight else None,
            }

            return Response({
                "company": company_data,
                "campaign": {
                    "id": str(campaign.id),
                    "name": campaign.name,
                    "product_description": campaign.product_description,
                    "problem_statement": campaign.problem_statement,
                } if campaign else None,
                "analysis": analysis_payload,
                "problem_hypothesis": problem_hypothesis,
                "scores": scores,
                "explanation": explanation,
                "signals": signals,
                "evidence_timeline": evidence,
                "source_summary": {
                    "verifiable_sources": len({item.source_url for item in evidence_records}),
                    "evidence_records": evidence_count,
                },
                "contacts": contacts,
                "buying_group": buying_group,
                "recommended_action": recommended_action,
                "talking_points": analysis_payload["talking_points"],
                "freshness": freshness,
                "data_locked": False,
                "research_status": research_status,
                "research_run_id": str(latest_research_attempt.id) if latest_research_attempt else None,
            }, status=status.HTTP_200_OK)
            
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)


def evaluate_smart_list(target_list: TargetList):
    criteria = target_list.criteria
    queryset = LeadCompany.objects.all()
    
    category = criteria.get("category")
    if category:
        queryset = queryset.filter(category__iexact=category)
        
    location = criteria.get("location")
    if location:
        queryset = queryset.filter(address__icontains=location)
        
    min_score = criteria.get("min_score")
    if min_score:
        queryset = queryset.filter(analysis__lead_score__gte=float(min_score))
        
    return queryset


class TargetListListCreateAPIView(APIView):
    """List and create target segment lists."""
    def get(self, request):
        lists = TargetList.objects.all()
        serializer = TargetListSerializer(lists, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = TargetListSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=get_default_user())
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TargetListDetailAPIView(APIView):
    """Retrieve details and memberships of specific target list."""
    def get(self, request, pk):
        try:
            target_list = TargetList.objects.get(id=pk)
            if target_list.is_smart:
                companies = evaluate_smart_list(target_list)
            else:
                companies = LeadCompany.objects.filter(list_memberships__target_list=target_list)
            
            company_serializer = LeadCompanySerializer(companies, many=True)
            list_serializer = TargetListSerializer(target_list)
            return Response({
                "list": list_serializer.data,
                "leads": company_serializer.data
            }, status=status.HTTP_200_OK)
        except TargetList.DoesNotExist:
            return Response({"error": "Target list not found"}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request, pk):
        """Add a manual membership to static lists."""
        try:
            target_list = TargetList.objects.get(id=pk)
            if target_list.is_smart:
                return Response({"error": "Cannot manually add member to smart list"}, status=status.HTTP_400_BAD_REQUEST)
            
            company_id = request.data.get("company_id")
            company = LeadCompany.objects.get(id=company_id)
            
            membership, created = ListMembership.objects.get_or_create(
                target_list=target_list,
                company=company
            )
            return Response({"status": "added", "created": created}, status=status.HTTP_200_OK)
        except (TargetList.DoesNotExist, LeadCompany.DoesNotExist):
            return Response({"error": "List or company not found"}, status=status.HTTP_404_NOT_FOUND)


class LeadSalesGuidanceAPIView(APIView):
    """Generates structured pitch talking points and outreach message drafts using LLM router."""
    def post(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            campaign_id = request.data.get("campaign_id")
            campaign = ProspectingCampaign.objects.get(id=campaign_id)
            
            person_id = request.data.get("person_id")
            person = None
            if person_id:
                person = Person.objects.filter(id=person_id, company=company).first()

            tone = request.data.get("tone", "professional")
            objective = request.data.get("objective", "book_meeting")

            # Collect evidence/signals context
            evidence = Evidence.objects.filter(company=company)
            evidence_text = "\n".join([f"- {ev.evidence_text} (Source: {ev.source_url})" for ev in evidence[:5]])

            prompt = (
                f"Create sales outreach guidance for target account '{company.name}' "
                f"in campaign '{campaign.name}'. Product values: '{campaign.product_description}'.\n"
                f"Contact person: {person.name if person else 'Operations Manager'} (Title: {person.title if person else 'Ops Manager'}).\n"
                f"Tone: {tone}. Outreach objective: {objective}.\n"
                f"Observed account evidence:\n{evidence_text}\n"
            )

            schema = (
                "{"
                '  "talking_points": ["string (value statement mapped to evidence)"],'
                '  "recommended_angle": "string (value hook)",'
                '  "recommended_next_step": "string (next CTA)",'
                '  "message_draft": "string (email pitch copy)",'
                '  "risks": ["string"],'
                '  "unknowns": ["string"]'
                "}"
            )

            system_prompt = "You are a senior AI sales development and copywriting strategist. Return ONLY raw JSON."
            full_prompt = f"{prompt}\n\nSchema:\n{schema}\n\nReturn ONLY raw JSON."
            
            import json
            from llm.context import LLMRequestContext
            with LLMRequestContext(
                correlation_id=f"lead_guidance:{pk}",
                operation="prospecting.lead_guidance",
                metadata={
                    "company_id": str(company.id),
                    "company_name": company.name,
                    "campaign_id": str(campaign.id),
                    "person_id": str(person.id) if person else "",
                }
            ):
                result = router.generate(
                    prompt=full_prompt,
                    system_prompt=system_prompt,
                    prompt_key="prospecting.lead_guidance.user",
                    system_prompt_key="prospecting.lead_guidance.system",
                    template_variables={
                        "company_name": company.name,
                        "campaign_name": campaign.name,
                        "product_description": campaign.product_description,
                        "contact_name": person.name if person else 'Operations Manager',
                        "contact_title": person.title if person else 'Ops Manager',
                        "tone": tone,
                        "objective": objective,
                        "evidence": evidence_text
                    }
                )
            text = result.get("text", "").strip()

            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            data = json.loads(text)
            
            # Save SalesGuidance
            guidance = SalesGuidance.objects.create(
                company=company,
                campaign=campaign,
                person=person,
                talking_points=data.get("talking_points", []),
                recommended_angle=data.get("recommended_angle", "Direct Pitch"),
                recommended_next_step=data.get("recommended_next_step", "Email pitch"),
                message_draft=data.get("message_draft", ""),
                risks=data.get("risks", []),
                unknowns=data.get("unknowns", []),
                metadata={"tone": tone, "objective": objective}
            )

            serializer = SalesGuidanceSerializer(guidance)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except (LeadCompany.DoesNotExist, ProspectingCampaign.DoesNotExist) as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error generating sales guidance: {e}")
            return Response({"error": "Failed to generate sales guidance"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CampaignEnrollmentAPIView(APIView):
    """Manage lead enrollments to prospecting campaigns."""
    def get(self, request):
        campaign_id = request.query_params.get("campaign_id")
        if not campaign_id:
            return Response({"error": "campaign_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        enrollments = CampaignEnrollment.objects.filter(campaign_id=campaign_id)
        serializer = CampaignEnrollmentSerializer(enrollments, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        campaign_id = request.data.get("campaign_id")
        company_id = request.data.get("company_id")
        status_val = request.data.get("status", "ENROLLED").upper()

        if status_val not in ["ELIGIBLE", "ENROLLED", "PAUSED", "EXCLUDED", "COMPLETED"]:
            return Response({"error": "Invalid enrollment status"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            campaign = ProspectingCampaign.objects.get(id=campaign_id)
            company = LeadCompany.objects.get(id=company_id)
            
            enrollment, created = CampaignEnrollment.objects.update_or_create(
                campaign=campaign,
                company=company,
                defaults={"status": status_val}
            )
            
            serializer = CampaignEnrollmentSerializer(enrollment)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except (ProspectingCampaign.DoesNotExist, LeadCompany.DoesNotExist) as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)


class EmailSequenceListCreateAPIView(APIView):
    """List and create email outreach sequences for campaigns."""
    def get(self, request):
        campaign_id = request.query_params.get("campaign_id")
        if not campaign_id:
            return Response({"error": "campaign_id query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        sequences = EmailSequence.objects.filter(campaign_id=campaign_id)
        serializer = EmailSequenceSerializer(sequences, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = EmailSequenceSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EmailMessageListCreateAPIView(APIView):
    """List and create email message drafts for companies/sequences."""
    def get(self, request):
        company_id = request.query_params.get("company_id")
        if not company_id:
            return Response({"error": "company_id query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        messages = EmailMessage.objects.filter(company_id=company_id)
        serializer = EmailMessageSerializer(messages, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = EmailMessageSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EmailMessageSendAPIView(APIView):
    """Triggers the MockEmailProvider safety checks and sends email message."""
    def post(self, request, pk):
        try:
            message = EmailMessage.objects.get(id=pk)
            from prospecting.email.email_provider import MockEmailProvider
            provider = MockEmailProvider()
            success = provider.send(message)
            if success:
                return Response({"status": "sent", "sent_at": message.sent_at.isoformat()}, status=status.HTTP_200_OK)
            return Response({"status": "blocked", "reason": f"Safety checks failed. Status set to: {message.status}"}, status=status.HTTP_400_BAD_REQUEST)
        except EmailMessage.DoesNotExist:
            return Response({"error": "Email message not found"}, status=status.HTTP_404_NOT_FOUND)


class InboundReplyListCreateAPIView(APIView):
    """Receive and automatically classify inbound reply intents using LLM router."""
    def post(self, request):
        message_id = request.data.get("email_message_id")
        reply_text = request.data.get("reply_text")

        try:
            message = EmailMessage.objects.get(id=message_id)
            
            # Create reply draft
            reply = InboundReply.objects.create(
                email_message=message,
                reply_text=reply_text,
                classification='UNKNOWN'
            )

            # Classify
            from prospecting.email.reply_classifier import ReplyClassifier
            res = ReplyClassifier.classify_reply(reply)
            
            # Trigger real-time campaign event notifications if positive/unsubscribe trigger
            if res["classification"] == "INTERESTED" and message.company.campaign:
                from prospecting.consumers import broadcast_campaign_event
                broadcast_campaign_event(
                    campaign_id=str(message.company.campaign.id),
                    event_type="POSITIVE_REPLY",
                    metadata={
                        "company_name": message.company.name,
                        "recipient": message.recipient_email,
                        "reply_text": reply_text[:200]
                    }
                )

            serializer = InboundReplySerializer(reply)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except EmailMessage.DoesNotExist:
            return Response({"error": "Email message not found"}, status=status.HTTP_404_NOT_FOUND)


class LeadFeedbackAPIView(APIView):
    """Add manual human review feedback to leads."""
    def post(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            feedback_type = request.data.get("feedback_type", "").upper()
            notes = request.data.get("notes", "")

            allowed = ['USEFUL', 'WRONG_MATCH', 'BAD_EVIDENCE', 'GOOD_EVIDENCE', 'GOOD_SIGNAL', 'BAD_SIGNAL', 'APPROVED', 'REJECTED']
            if feedback_type not in allowed:
                return Response({"error": "Invalid feedback type"}, status=status.HTTP_400_BAD_REQUEST)

            feedback = LeadFeedback.objects.create(
                company=company,
                feedback_type=feedback_type,
                notes=notes
            )
            serializer = LeadFeedbackSerializer(feedback)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)


class DashboardOverviewAPIView(APIView):
    """Aggregate top-level funnel overview analytics."""
    def get(self, request):
        campaign_id = request.query_params.get("campaign_id")
        
        companies = LeadCompany.objects.all()
        if campaign_id:
            companies = companies.filter(campaign_id=campaign_id)

        discovered = companies.count()
        qualified = companies.filter(analysis__lead_score__gte=70).count()
        
        emails = EmailMessage.objects.filter(company__in=companies)
        contacted = emails.filter(status='SENT').values('company').distinct().count()
        
        replies = InboundReply.objects.filter(email_message__company__in=companies)
        replied = replies.values('email_message__company').distinct().count()
        positive = replies.filter(classification='INTERESTED').values('email_message__company').distinct().count()

        return Response({
            "discovered": discovered,
            "qualified": qualified,
            "contacted": contacted,
            "replied": replied,
            "positive": positive
        }, status=status.HTTP_200_OK)


class DashboardSignalsAPIView(APIView):
    """Retrieve signals performance metrics."""
    def get(self, request):
        from django.db.models import Count, Q
        campaign_id = request.query_params.get("campaign_id")
        
        signals = CompanySignal.objects.all()
        if campaign_id:
            signals = signals.filter(company__campaign_id=campaign_id)

        # Count total detections vs good signals feedback
        performance = signals.values('signal__name', 'signal__category').annotate(
            total_detections=Count('id'),
            active_count=Count('id', filter=Q(status='ACTIVE'))
        ).order_by('-total_detections')

        return Response(list(performance), status=status.HTTP_200_OK)


class DashboardFunnelAPIView(APIView):
    """Funnel stages transition rates."""
    def get(self, request):
        campaign_id = request.query_params.get("campaign_id")
        
        companies = LeadCompany.objects.all()
        if campaign_id:
            companies = companies.filter(campaign_id=campaign_id)

        total = companies.count()
        if total == 0:
            return Response({"error": "No leads found"}, status=status.HTTP_200_OK)

        qualified = companies.filter(analysis__lead_score__gte=70).count()
        
        emails = EmailMessage.objects.filter(company__in=companies)
        contacted = emails.filter(status='SENT').values('company').distinct().count()

        replies = InboundReply.objects.filter(email_message__company__in=companies)
        replied = replies.values('email_message__company').distinct().count()

        return Response({
            "stages": [
                {"stage": "Discovered", "count": total, "conversion": 100.0},
                {"stage": "Qualified", "count": qualified, "conversion": round((qualified / total) * 100, 2)},
                {"stage": "Contacted", "count": contacted, "conversion": round((contacted / total) * 100, 2)},
                {"stage": "Replied", "count": replied, "conversion": round((replied / total) * 100, 2)},
            ]
        }, status=status.HTTP_200_OK)


class DashboardOpportunitiesAPIView(APIView):
    """Map geographical & industry trends grouped using Postgres aggregates."""
    def get(self, request):
        from django.db.models import Avg, Count
        campaign_id = request.query_params.get("campaign_id")
        
        companies = LeadCompany.objects.all()
        if campaign_id:
            companies = companies.filter(campaign_id=campaign_id)

        # Group by category/industry and address/geography
        trends = companies.values('category', 'address').annotate(
            count=Count('id'),
            average_score=Avg('analysis__lead_score')
        ).order_by('-count')[:50]

        return Response(list(trends), status=status.HTTP_200_OK)


class LeadCRMSyncAPIView(APIView):
    """Synchronize qualified lead company and buying group contacts to external CRM."""
    def post(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            owner_email = request.data.get("owner_email")
            if not owner_email and company.campaign:
                owner_email = company.campaign.created_by.email

            from prospecting.crm.crm_provider import MockCRMProvider
            provider = MockCRMProvider()

            # 1. Sync company details
            comp_id = provider.upsert_company(company)
            
            # 2. Sync all buying group contacts/members
            people = Person.objects.filter(company=company)
            for person in people:
                provider.upsert_contact(person)

            # 3. Update stage and assign owner
            provider.update_stage(company, "Prospecting")
            if owner_email:
                provider.assign_owner(company, owner_email)

            # Retrieve saved records
            records = CRMIntegrationRecord.objects.filter(
                Q(company=company) | Q(person__company=company)
            )
            serializer = CRMIntegrationRecordSerializer(records, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)


from prospecting.serializers import (
    ProspectingRequestSerializer,
    ProspectingSpecificationVersionSerializer,
    DiscoverySerializer
)
from prospecting.intent.service import ProspectingIntentService
from prospecting.intent.schemas import ProspectingSpecification
from prospecting.intent.validator import SpecificationValidationError
from prospecting.models import ProspectingRequest, ProspectingSpecificationVersion, Discovery

class ProspectingIntakeAPIView(APIView):
    """Create a new prospecting request or list existing ones."""
    def get(self, request):
        user = get_default_user()
        requests = ProspectingRequest.objects.filter(user_profile=user).order_by('-created_at')
        serializer = ProspectingRequestSerializer(requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        from prospecting.intent.service import ProspectingIntentService
        user = get_default_user()
        objective = request.data.get("objective", "").strip()
        target = request.data.get("target", "").strip()
        qualification = request.data.get("qualification", "").strip()

        if not objective:
            return Response({"error": "objective is required"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Create request
        req = ProspectingIntentService.create_intake_request(
            user_profile=user,
            objective=objective,
            target=target,
            qualification=qualification
        )

        # 2. Parse request intent asynchronously via Celery
        try:
            req.status = 'PARSING'
            req.save()

            from prospecting.tasks import parse_intent_async
            parse_intent_async.delay(str(req.id))

            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "specification_version": None
            }, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            logger.exception("Failed to dispatch Celery intent parser task")
            req.status = 'FAILED'
            req.save()
            return Response({
                "error": f"Celery task dispatch failed: {str(e)}. Please ensure Celery worker and broker are running.",
                "request": ProspectingRequestSerializer(req).data
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingIntakeDetailAPIView(APIView):
    """Retrieve detailed prospecting request metadata and specifications list."""
    def get(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            versions = req.spec_versions.order_by('-version')
            latest_run = req.discovery_runs.order_by('-started_at').first()
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "versions": ProspectingSpecificationVersionSerializer(versions, many=True).data,
                "latest_run": {
                    "run_id": str(latest_run.id),
                    "keyword": latest_run.keyword,
                    "location": latest_run.location,
                    "status": latest_run.status,
                } if latest_run else None,
            }, status=status.HTTP_200_OK)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)


class ProspectingIntakeParseAPIView(APIView):
    """Trigger a new intent parsing run manually."""
    def post(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            from prospecting.tasks import parse_intent_async
            req.status = 'PARSING'
            req.save()
            parse_intent_async.delay(str(req.id))
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "message": "Parsing task dispatched successfully."
            }, status=status.HTTP_202_ACCEPTED)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingIntakeClarifyAPIView(APIView):
    """Submit a clarification answer and reparse updated intake parameters."""
    def post(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            question = request.data.get("question", "").strip()
            answer = request.data.get("answer", "").strip()

            if not question or not answer:
                return Response({"error": "question and answer are required"}, status=status.HTTP_400_BAD_REQUEST)

            # Update history and save
            history = list(req.clarification_history)
            if history and history[-1].get("answer") == "":
                history[-1]["answer"] = answer
            else:
                history.append({"question": question, "answer": answer})
            req.clarification_history = history
            req.status = 'PARSING'
            req.save()

            # Dispatch parsing task asynchronously
            from prospecting.tasks import parse_intent_async
            parse_intent_async.delay(str(req.id))

            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "message": "Clarification submitted. Reparsing..."
            }, status=status.HTTP_202_ACCEPTED)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingIntakeSpecificationAPIView(APIView):
    """Save an edited specification, creating a new draft/review version."""
    def patch(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            spec_json = request.data.get("specification_json")
            if not spec_json:
                return Response({"error": "specification_json is required"}, status=status.HTTP_400_BAD_REQUEST)

            spec_ver = ProspectingIntentService.update_specification(str(req.id), spec_json)
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "specification_version": ProspectingSpecificationVersionSerializer(spec_ver).data
            }, status=status.HTTP_200_OK)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)
        except SpecificationValidationError as ve:
            return Response({"errors": ve.errors}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as val_err:
            return Response({"error": str(val_err)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingIntakeConfirmAPIView(APIView):
    """Confirm a specific version, lock it, and dispatch discovery Celery workflow."""
    def post(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            version = request.data.get("version")
            if version is None:
                return Response({"error": "version is required"}, status=status.HTTP_400_BAD_REQUEST)

            discovery = ProspectingIntentService.confirm_specification(str(req.id), int(version), user)
            run = discovery.runs.order_by('-started_at').first()
            return Response({
                "message": "Specification confirmed and discovery run dispatched successfully.",
                "discovery": DiscoverySerializer(discovery).data,
                "run_id": str(run.id) if run else None,
            }, status=status.HTTP_200_OK)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)
        except SpecificationValidationError as ve:
            return Response({"errors": ve.errors}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as val_err:
            return Response({"error": str(val_err)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProspectingIntakeCancelAPIView(APIView):
    """Cancel a prospecting request workflow."""
    def post(self, request, pk):
        user = get_default_user()
        try:
            with transaction.atomic():
                req = ProspectingRequest.objects.select_for_update().get(id=pk, user_profile=user)
                active_runs = list(
                    req.discovery_runs.select_for_update().filter(status__in=['pending', 'queued', 'running'])
                )
                for run in active_runs:
                    run.status = 'cancelled'
                    run.completed_at = timezone.now()
                    run.save(update_fields=['status', 'completed_at'])

                req.status = 'CANCELLED'
                req.save(update_fields=['status', 'updated_at'])

            # Replace any cached running state immediately. The worker also
            # checks the database between stages and exits cooperatively.
            from django.core.cache import cache
            for run in active_runs:
                cache.set(f"discovery_run:{run.id}:progress", {
                    "stage": "cancelled",
                    "progress": 100,
                    "message": "Discovery run cancelled.",
                    "status": "cancelled",
                }, timeout=86400)
            return Response(ProspectingRequestSerializer(req).data, status=status.HTTP_200_OK)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)


class ProspectingDiscoverStatusAPIView(APIView):
    """Retrieve real-time status and metrics of a Discovery Run."""
    def get(self, request, pk):
        from django.core.cache import cache
        from prospecting.models import DiscoveryRun, LeadCompany
        user = get_default_user()
        try:
            run = DiscoveryRun.objects.get(id=pk, user_profile=user)
        except DiscoveryRun.DoesNotExist:
            # Intake confirmation historically returned a Discovery UUID, while
            # this endpoint expects a DiscoveryRun UUID. Accept both identifiers
            # so clients created against the old response continue to work.
            run = (
                DiscoveryRun.objects
                .filter(discovery_id=pk, user_profile=user)
                .order_by('-started_at')
                .first()
            )
            if run is None:
                return Response({"error": "Discovery run not found"}, status=status.HTTP_404_NOT_FOUND)

        # 1. Read real-time progress from cache
        progress_data = cache.get(f"discovery_run:{run.id}:progress")
        if not progress_data:
            # Fallback based on database status
            if run.status == 'completed':
                progress_data = {
                    "stage": "completed",
                    "progress": 100,
                    "message": "Discovery and enrichment finished.",
                    "status": "completed"
                }
            elif run.status == 'failed':
                progress_data = {
                    "stage": "failed",
                    "progress": 100,
                    "message": "Discovery run failed.",
                    "status": "failed"
                }
            elif run.status == 'cancelled':
                progress_data = {
                    "stage": "cancelled",
                    "progress": 100,
                    "message": "Discovery run cancelled.",
                    "status": "cancelled"
                }
            else:
                progress_data = {
                    "stage": "queued",
                    "progress": 5,
                    "message": "Initializing task runner...",
                    "status": "running"
                }

        # 2. Read metrics from cache or fallback
        metrics_data = cache.get(f"discovery_run:{run.id}:metrics")
        if not metrics_data:
            new_leads = LeadCompany.objects.filter(discovery_run=run).count()
            discovered = run.total_leads_found or new_leads
            duplicates = max(0, discovered - new_leads)
            metrics_data = {
                "discovered": discovered,
                "new": new_leads,
                "duplicates": duplicates
            }

        return Response({
            "run_id": str(run.id),
            "status": run.status,
            "stage": progress_data["stage"],
            "progress": progress_data["progress"],
            "message": progress_data["message"],
            "metrics": metrics_data
        }, status=status.HTTP_200_OK)

