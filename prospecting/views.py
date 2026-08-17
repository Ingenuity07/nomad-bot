import os
import logging
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from knowledge_base.models import UserProfile

from prospecting.models import (
    DiscoveryRun, LeadCompany, LeadContact, WebsiteAnalysis, ProblemSignal,
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
    CRMIntegrationRecordSerializer
)
from prospecting.discovery.engine import BusinessDiscoveryEngine
from prospecting.contact import ContactExtractor
from prospecting.analyzer import WebsiteAnalyzer
from llm.router import IntelligentRouter

logger = logging.getLogger(__name__)
router = IntelligentRouter()

def get_default_user():
    user, _ = UserProfile.objects.get_or_create(
        username='default_user',
        defaults={'email': 'default@example.com', 'full_name': 'Shivam Singh'}
    )
    return user

from prospecting.tasks import discover_campaign_async

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

        try:
            is_dev = os.environ.get("DEV", "False").lower() in ("true", "1", "yes")
            if is_dev:
                # Process synchronously
                result = discover_campaign_async(str(run.id))
                return Response({
                    "status": "success",
                    "run_id": str(run.id),
                    "message": "Discovery run executed synchronously.",
                    "result": result
                }, status=status.HTTP_200_OK)
            else:
                # Dispatch asynchronous celery task
                discover_campaign_async.delay(str(run.id))
                return Response({
                    "status": "success",
                    "run_id": str(run.id),
                    "message": "Discovery run queued successfully."
                }, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception("Failed to execute or dispatch prospecting task")
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
        workspace = get_default_workspace()
        queryset = LeadCompany.objects.filter(
            Q(campaign__workspace=workspace) |
            Q(discovery_run__campaign__workspace=workspace) |
            Q(campaign__isnull=True, discovery_run__campaign__isnull=True)
        ).distinct()
        
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


class LeadResearchAPIView(APIView):
    """Triggers the LangGraph website research graph loop asynchronously for the lead company."""
    def post(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            from prospecting.workflows.research_graph import website_research_graph
            res = website_research_graph.invoke({
                "company_id": str(company.id),
                "campaign_id": str(company.campaign.id) if company.campaign else None,
                "research_goal": "Perform automated account research on matching signals."
            })
            return Response({"status": "research_completed", "visited_urls": res.get("visited_urls", [])}, status=status.HTTP_200_OK)
        except LeadCompany.DoesNotExist:
            return Response({"error": "Lead company not found"}, status=status.HTTP_404_NOT_FOUND)


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
    """Compiles single payload of account intelligence summary details."""
    def get(self, request, pk):
        try:
            company = LeadCompany.objects.get(id=pk)
            latest_qual = company.qualifications.order_by('-analysis_version').first()
            analysis = getattr(company, 'analysis', None)

            # Build components
            company_data = LeadCompanySerializer(company).data
            
            problem_hypothesis = ""
            if company.campaign:
                problem_hypothesis = company.campaign.problem_statement
                
            scores = {
                "problem_fit": float(latest_qual.problem_fit_score) if latest_qual else (float(analysis.lead_score) * 10 if analysis else 0.0),
                "evidence_strength": float(latest_qual.evidence_strength_score) if latest_qual else 50.0,
                "buying_window": float(latest_qual.buying_window_score) if latest_qual else 50.0,
                "overall": float(latest_qual.overall_score) if latest_qual else (float(analysis.lead_score) * 10 if analysis else 0.0),
            }

            explanation = {
                "overall_classification": latest_qual.explanation.get("overall_classification", "UNKNOWN") if latest_qual else "UNKNOWN",
                "positive_factors": latest_qual.positive_factors if latest_qual else [],
                "negative_factors": latest_qual.negative_factors if latest_qual else [],
                "unknowns": latest_qual.unknowns if latest_qual else []
            }

            signals = CompanySignalSerializer(company.signals.all(), many=True).data
            evidence = EvidenceSerializer(company.evidence_records.all(), many=True).data
            contacts = PersonSerializer(company.people.all(), many=True).data
            buying_group = BuyingGroupMemberSerializer(company.buying_group_members.all(), many=True).data
            
            recommended_action = "HIGH_PRIORITY_OUTREACH" if scores["overall"] >= 75.0 else "ENRICH_AND_MONITOR"
            
            freshness = {
                "last_researched": company.created_at.isoformat(),
                "is_fresh": True
            }

            return Response({
                "company": company_data,
                "problem_hypothesis": problem_hypothesis,
                "scores": scores,
                "explanation": explanation,
                "signals": signals,
                "evidence_timeline": evidence,
                "source_summary": {
                    "verifiable_sources": len(evidence)
                },
                "contacts": contacts,
                "buying_group": buying_group,
                "recommended_action": recommended_action,
                "freshness": freshness
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
            result = router.generate(prompt=full_prompt, system_prompt=system_prompt)
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
            owner_email = request.data.get("owner_email", "owner@nomad.ai")

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

        # 2. Parse request intent immediately
        try:
            spec_ver = ProspectingIntentService.parse_request(str(req.id))
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "specification_version": ProspectingSpecificationVersionSerializer(spec_ver).data
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception("Failed to parse request intent during post")
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "error": str(e)
            }, status=status.HTTP_201_CREATED)


class ProspectingIntakeDetailAPIView(APIView):
    """Retrieve detailed prospecting request metadata and specifications list."""
    def get(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            versions = req.spec_versions.order_by('-version')
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "versions": ProspectingSpecificationVersionSerializer(versions, many=True).data
            }, status=status.HTTP_200_OK)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)


class ProspectingIntakeParseAPIView(APIView):
    """Trigger a new intent parsing run manually."""
    def post(self, request, pk):
        user = get_default_user()
        try:
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            spec_ver = ProspectingIntentService.parse_request(str(req.id))
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "specification_version": ProspectingSpecificationVersionSerializer(spec_ver).data
            }, status=status.HTTP_200_OK)
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

            spec_ver = ProspectingIntentService.submit_clarification(str(req.id), question, answer)
            return Response({
                "request": ProspectingRequestSerializer(req).data,
                "specification_version": ProspectingSpecificationVersionSerializer(spec_ver).data
            }, status=status.HTTP_200_OK)
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
            return Response({
                "message": "Specification confirmed and discovery run dispatched successfully.",
                "discovery": DiscoverySerializer(discovery).data
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
            req = ProspectingRequest.objects.get(id=pk, user_profile=user)
            if req.status == 'CONFIRMED':
                return Response({"error": "Cannot cancel a request that has already been confirmed."}, status=status.HTTP_400_BAD_REQUEST)
            req.status = 'CANCELLED'
            req.save()
            return Response(ProspectingRequestSerializer(req).data, status=status.HTTP_200_OK)
        except ProspectingRequest.DoesNotExist:
            return Response({"error": "Prospecting request not found"}, status=status.HTTP_404_NOT_FOUND)

