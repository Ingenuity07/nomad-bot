from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch, MagicMock
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from knowledge_base.models import UserProfile
from prospecting.exceptions import NormalizationError
from prospecting.models import (
    DiscoveryRun, LeadCompany, LeadContact, WebsiteAnalysis,
    Workspace, ProspectingCampaign, ICPProfile, ProblemSignal, Evidence, CompanySignal, Qualification,
    Person, ContactPoint, BuyingGroupMember, TargetList, CampaignEnrollment, SalesGuidance,
    EmailSequence, EmailMessage, EmailBounce, EmailUnsubscribe, InboundReply, LeadFeedback
)
from prospecting.discovery.dto import DiscoveryRequest, DiscoveryResult, DiscoveryResultItem
from prospecting.discovery.normalizer import Normalizer
from prospecting.discovery.deduplication import Deduplicator
from prospecting.discovery.providers.registry import discovery_provider_registry
from prospecting.tasks import discover_campaign_async, build_duckduckgo_queries
from prospecting.workflows.graphs import strategy_formulator_graph
from prospecting.workflows.research_graph import website_research_graph
from prospecting.qualification.scoring import OverallQualificationScorer
from prospecting.qualification.buying_group import BuyingGroupWorkflow

def get_default_user():
    user, _ = UserProfile.objects.get_or_create(
        username='test_user',
        defaults={'email': 'test@example.com', 'full_name': 'Test User'}
    )
    return user

def get_default_workspace():
    workspace, _ = Workspace.objects.get_or_create(
        name="Default Workspace",
        defaults={"timezone": "UTC"}
    )
    return workspace


class DiscoveryDTOTestCase(TestCase):
    def test_duckduckgo_queries_are_short_and_have_fallbacks(self):
        queries = build_duckduckgo_queries("pest control", "Manchester, UK")
        self.assertEqual(queries[0], '\"pest control\" \"Manchester, UK\"')
        self.assertIn("companies", queries[1])
        self.assertIn("directory", queries[2])

    def test_valid_request(self):
        req = DiscoveryRequest(
            query="pest control",
            location="Manchester, UK",
            latitude=53.4808,
            longitude=-2.2426,
            radius_meters=10000,
            limit=10
        )
        req.validate()

    def test_invalid_query(self):
        req = DiscoveryRequest(query="", location="London")
        with self.assertRaises(NormalizationError):
            req.validate()


class ProviderRegistryTestCase(TestCase):
    def test_registered_providers(self):
        self.assertTrue(discovery_provider_registry.has("google_places"))
        self.assertTrue(discovery_provider_registry.has("apify"))
        self.assertTrue(discovery_provider_registry.has("search"))


class NormalizerTestCase(TestCase):
    def test_normalize_name(self):
        self.assertEqual(Normalizer.normalize_name("ABC Pest Control Ltd."), "abc pest control")

    def test_normalize_domain(self):
        self.assertEqual(Normalizer.normalize_domain("https://www.google.com/search?q=1"), "google.com")

    def test_normalize_phone(self):
        self.assertEqual(Normalizer.normalize_phone("+1 (555) 123-4567"), "+15551234567")


class DeduplicatorTestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="hvac",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Manchester HVAC Specialists Ltd.",
            website="https://www.manchester-hvac.co.uk",
            phone="+441611234567"
        )

    def test_dedup_by_domain(self):
        item = DiscoveryResultItem(
            name="HVAC Manchester",
            website="http://manchester-hvac.co.uk/about-us"
        )
        matched = Deduplicator.find_existing_company(item)
        self.assertEqual(matched, self.company)


class CeleryTaskTestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="pest control",
            location="Manchester"
        )

    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.WebsiteAnalyzer")
    @patch("prospecting.tasks.ContactExtractor")
    @patch("prospecting.tasks.broadcast_progress")
    @patch("prospecting.tasks.broadcast_completion")
    def test_discover_campaign_async_flow(
        self, mock_broadcast_completion, mock_broadcast_progress, mock_contact_extractor,
        mock_website_analyzer, mock_execute
    ):
        mock_provider = MagicMock()
        mock_provider.health_check.return_value = True
        mock_provider.search.return_value = DiscoveryResult(
            provider="mock_provider",
            request_id="test-req-123",
            results=[
                DiscoveryResultItem(
                    name="Pest Control Pro",
                    website="https://pestcontrolpro.co.uk",
                    phone="+441619999999",
                    address="Manchester, UK",
                    category="Pest Control"
                )
            ]
        )

        mock_execute.return_value = type("ToolResult", (object,), {
            "success": True,
            "data": {
                "companies": [{
                    "name": "Pest Control Pro",
                    "website": "https://pestcontrolpro.co.uk",
                    "phone": "+441619999999",
                    "address": "Manchester, UK",
                    "category": "Pest Control",
                    "external_id": "test-company-1",
                    "raw_metadata": {}
                }]
            }
        })()

        with patch.dict(discovery_provider_registry._providers, {"google_places": mock_provider}):
            result = discover_campaign_async(str(self.run.id))
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["leads_found"], 1)

        called_tools = [call.args[0] for call in mock_execute.call_args_list]
        self.assertIn("search_companies", called_tools)
        self.assertIn("search_web", called_tools)

    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.WebsiteAnalyzer")
    @patch("prospecting.tasks.ContactExtractor")
    @patch("prospecting.tasks.broadcast_progress")
    @patch("prospecting.tasks.broadcast_completion")
    def test_discovery_continues_to_duckduckgo_after_company_provider_failure(
        self, mock_broadcast_completion, mock_broadcast_progress, mock_contact_extractor,
        mock_website_analyzer, mock_execute
    ):
        def execute_side_effect(tool_name, arguments, context=None):
            if tool_name == "search_web":
                return type("ToolResult", (object,), {
                    "success": True,
                    "data": {
                        "results": [{
                            "title": "Manchester Pest Experts",
                            "name": "Manchester Pest Experts",
                            "url": "https://pest-experts.example",
                            "snippet": "Local pest-control company"
                        }]
                    }
                })()
            return type("ToolResult", (object,), {
                "success": False,
                "data": None
            })()

        mock_execute.side_effect = execute_side_effect

        result = discover_campaign_async(str(self.run.id))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["leads_found"], 1)
        self.assertTrue(LeadCompany.objects.filter(name="Manchester Pest Experts").exists())
        called_tools = [call.args[0] for call in mock_execute.call_args_list]
        self.assertIn("search_companies", called_tools)
        self.assertIn("search_web", called_tools)


class StrategyFormulationTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Route Optimization",
            product_description="GPS dispatch tool",
            problem_statement="Manual routing errors",
            created_by=self.user
        )

    @patch("prospecting.workflows.graphs.router.generate")
    def test_strategy_formulator_graph(self, mock_generate):
        mock_generate.side_effect = [
            {"type": "text", "text": '{"product_name": "GPS dispatch tool", "core_capabilities": ["route optimization"], "value_proposition": "save fuel"}'},
            {"type": "text", "text": '{"problems": [{"problem_name": "manual dispatching", "symptoms": ["delayed ETA"], "business_impact": "wasted fuel"}]}'},
            {"type": "text", "text": '{"hypotheses": [{"industry": "pest control", "company_size": "medium", "target_roles": ["ops manager"], "rationale": "fleet complexity"}]}'},
            {"type": "text", "text": '{"signals": [{"name": "hiring drivers", "category": "HIRING", "description": "hiring courier drivers", "signal_type": "website", "detection_method": "scraper", "weight": 1.0}]}'},
            {"type": "text", "text": '{"search_queries": [{"industry": "pest control", "query": "pest control services", "location": "Manchester", "priority": 1, "reason": "high field presence"}]}'}
        ]

        state = {
            "campaign_id": str(self.campaign.id),
            "input_description": "GPS dispatch tool for field pest control services."
        }

        res = strategy_formulator_graph.invoke(state)
        self.assertIn("product_model", res)
        self.assertEqual(res["validation_errors"], [])


class WebsiteResearchTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="pest control",
            location="Manchester"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Manchester Pest Force Ltd.",
            website="https://www.manchesterpestforce.co.uk",
            phone="+441618888888"
        )

    @patch("prospecting.workflows.research_graph.router.generate")
    @patch("prospecting.workflows.research_graph.browser_tool.execute")
    def test_website_research_graph_flow(self, mock_browser_run, mock_generate):
        mock_browser_run.side_effect = [
            "Successfully navigated to https://www.manchesterpestforce.co.uk (Status: 200).",
            'Welcome to Manchester Pest Force! Check our <a href="https://www.manchesterpestforce.co.uk/careers">careers</a> page.',
            "Successfully navigated to https://www.manchesterpestforce.co.uk/careers (Status: 200).",
            "Careers page. We are hiring courier drivers and field technicians for pest control scheduling visits."
        ]

        mock_generate.side_effect = [
            {"type": "text", "text": '{"facts": []}'},
            {"type": "text", "text": '{"facts": [{"claim": "hiring courier and delivery drivers", "quoted_text": "We are hiring courier drivers and field technicians", "confidence": 0.95}, {"claim": "scheduling field visits for pest control", "quoted_text": "scheduling visits", "confidence": 0.95}]}'},
            {"type": "text", "text": "This company offers commercial pest control and is hiring courier drivers."}
        ]

        state = {
            "company_id": str(self.company.id),
            "campaign_id": None,
            "research_goal": "Check operational presence."
        }

        res = website_research_graph.invoke(state)
        self.assertIn("visited_urls", res)
        self.assertEqual(res["step_count"], 2)

        analysis = WebsiteAnalysis.objects.get(company=self.company)
        self.assertTrue(analysis.has_delivery)
        self.assertTrue(analysis.has_scheduling)
        self.assertEqual(analysis.lead_score, 10.0)
        self.assertIn("hiring courier drivers", analysis.description)

        self.assertEqual(Evidence.objects.filter(company=self.company).count(), 2)
        ev = Evidence.objects.filter(company=self.company, evidence_text__icontains="courier").first()
        self.assertIsNotNone(ev)


class ProblemSignalAPITestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.signal = ProblemSignal.objects.create(
            workspace=self.workspace,
            name="hiring mechanics",
            category="HIRING",
            description="hiring mechanics details",
            signal_type="website",
            detection_method="scraper"
        )

    def test_list_signals(self):
        url = reverse("prospecting-signals-list")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["name"], "hiring mechanics")

    def test_create_signal(self):
        url = reverse("prospecting-signals-list")
        payload = {
            "name": "fleet expansion",
            "category": "EXPANSION",
            "description": "fleet size growth",
            "signal_type": "website",
            "detection_method": "scraper",
            "weight": 1.5
        }
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "fleet expansion")
        self.assertTrue(ProblemSignal.objects.filter(name="fleet expansion").exists())

    def test_retrieve_signal(self):
        url = reverse("prospecting-signals-detail", kwargs={"pk": str(self.signal.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["name"], "hiring mechanics")

    def test_update_signal(self):
        url = reverse("prospecting-signals-detail", kwargs={"pk": str(self.signal.id)})
        payload = {"weight": 2.0}
        res = self.client.patch(url, data=payload, content_type="application/json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.weight, 2.0)

    def test_soft_delete_signal(self):
        url = reverse("prospecting-signals-detail", kwargs={"pk": str(self.signal.id)})
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.signal.refresh_from_db()
        self.assertFalse(self.signal.active)


class QualificationScoringTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Pest Campaign",
            product_description="Pest control scheduler",
            problem_statement="delayed ETA times",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="pest",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds Pest Force Ltd.",
            website="https://leedspestforce.co.uk",
            phone="+441132222222"
        )
        self.signal = ProblemSignal.objects.create(
            workspace=self.workspace,
            name="hiring operators",
            category="HIRING",
            description="hiring ops details",
            signal_type="website",
            detection_method="scraper",
            weight=1.5
        )
        self.comp_signal = CompanySignal.objects.create(
            company=self.company,
            signal=self.signal,
            confidence=0.9,
            status='ACTIVE'
        )
        self.evidence = Evidence.objects.create(
            company=self.company,
            signal=self.signal,
            source_type="website",
            source_url="https://leedspestforce.co.uk/careers",
            evidence_text="Now hiring field operators",
            confidence=0.95
        )

    def test_qualification_scoring_calculations(self):
        qual = OverallQualificationScorer.run_scoring(self.company, self.campaign)
        self.assertEqual(qual.analysis_version, 1)
        self.assertEqual(qual.company, self.company)
        self.assertEqual(qual.campaign, self.campaign)
        self.assertAlmostEqual(float(qual.problem_fit_score), 70.25, places=2)
        self.assertAlmostEqual(float(qual.evidence_strength_score), 67.50, places=2)
        self.assertAlmostEqual(float(qual.buying_window_score), 75.00, places=2)
        self.assertAlmostEqual(float(qual.overall_score), 70.38, places=2)


class LeadAPIExpansionsTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Test Campaign",
            product_description="Test dispatch tool",
            problem_statement="routing problems",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            campaign=self.campaign,
            name="Leeds HVAC Pros Ltd.",
            website="https://leedshvacpros.co.uk",
            phone="+441133333333"
        )
        self.person = Person.objects.create(
            company=self.company,
            name="Shivam Singh",
            title="Operations Manager"
        )
        self.contact = ContactPoint.objects.create(
            person=self.person,
            type="EMAIL",
            value="shivam@leedshvacpros.co.uk"
        )
        self.member = BuyingGroupMember.objects.create(
            campaign=self.campaign,
            company=self.company,
            person=self.person,
            role_type="DECISION_MAKER",
            relevance_score=90,
            reason="Ops Manager manages dispatch"
        )

    def test_lead_detail_api(self):
        url = reverse("lead-detail", kwargs={"pk": str(self.company.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["name"], "Leeds HVAC Pros Ltd.")

    def test_lead_contacts_api(self):
        url = reverse("lead-contacts", kwargs={"pk": str(self.company.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["name"], "Shivam Singh")
        self.assertEqual(res.data[0]["contact_points"][0]["value"], "shivam@leedshvacpros.co.uk")

    def test_lead_buying_group_api(self):
        url = reverse("lead-buying-group", kwargs={"pk": str(self.company.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["role_type"], "DECISION_MAKER")

    def test_lead_intelligence_api(self):
        url = reverse("lead-intelligence", kwargs={"pk": str(self.company.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("company", res.data)
        self.assertIn("scores", res.data)
        self.assertIn("explanation", res.data)
        self.assertIn("contacts", res.data)
        self.assertIn("buying_group", res.data)


class BuyingGroupWorkflowTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="GPS Dispatch Campaign",
            product_description="GPS field team tracking software",
            problem_statement="unknown route locations",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="fleet",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds Delivery Co.",
            website="https://leedsdelivery.co.uk"
        )

    @patch("prospecting.qualification.buying_group.router.generate")
    def test_buying_group_workflow_generation(self, mock_generate):
        mock_generate.return_value = {
            "type": "text",
            "text": (
                '{"people": [{'
                '  "name": "Shivam Singh",'
                '  "first_name": "Shivam",'
                '  "last_name": "Singh",'
                '  "title": "Director of Logistics",'
                '  "linkedin_url": "https://linkedin.com/in/shivam",'
                '  "role_type": "DECISION_MAKER",'
                '  "relevance_score": 95,'
                '  "reason": "Directs logistics fleet tools",'
                '  "contact_points": [{'
                '    "type": "EMAIL",'
                '    "value": "shivam@leedsdelivery.co.uk"'
                '  }]'
                '}]}'
            )
        }

        members = BuyingGroupWorkflow.run(
            self.company,
            self.campaign,
            scraped_text="Our logistics division is led by Shivam Singh, Director of Logistics."
        )

        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].role_type, "DECISION_MAKER")
        self.assertEqual(members[0].relevance_score, 95)

        # Check database records
        person = Person.objects.get(company=self.company, name="Shivam Singh")
        self.assertEqual(person.title, "Director of Logistics")
        self.assertTrue(ContactPoint.objects.filter(person=person, value="shivam@leedsdelivery.co.uk").exists())


class TargetListsTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="pest",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds Pest Control Inc.",
            category="Pest Control",
            address="Leeds, UK"
        )
        self.target_list = TargetList.objects.create(
            workspace=self.workspace,
            name="Pest Segment",
            is_smart=False,
            created_by=self.user
        )

    def test_static_list_membership(self):
        # Add manual member
        url = reverse("target-lists-detail", kwargs={"pk": str(self.target_list.id)})
        payload = {"company_id": str(self.company.id)}
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        
        # Verify member listed
        res_get = self.client.get(url)
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_get.data["leads"]), 1)
        self.assertEqual(res_get.data["leads"][0]["name"], "Leeds Pest Control Inc.")

    def test_smart_list_evaluation(self):
        smart_list = TargetList.objects.create(
            workspace=self.workspace,
            name="Pest Smart Segment",
            is_smart=True,
            criteria={"category": "Pest Control"},
            created_by=self.user
        )
        url = reverse("target-lists-detail", kwargs={"pk": str(smart_list.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["leads"]), 1)
        self.assertEqual(res.data["leads"][0]["name"], "Leeds Pest Control Inc.")


class CampaignEnrollmentTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Enroller Campaign",
            product_description="some product",
            problem_statement="delayed timing",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="HVAC Enrollee Ltd."
        )

    def test_enrollment_lifecycle(self):
        url = reverse("campaign-enrollments")
        
        # Enroll lead company
        payload = {
            "campaign_id": str(self.campaign.id),
            "company_id": str(self.company.id),
            "status": "ENROLLED"
        }
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "ENROLLED")

        # Verify query filters
        res_get = self.client.get(f"{url}?campaign_id={self.campaign.id}")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_get.data), 1)
        self.assertEqual(res_get.data[0]["status"], "ENROLLED")


class SalesGuidanceTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Sales Pitcher",
            product_description="Route optimizer for fleet deliveries",
            problem_statement="inefficient routes",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="courier",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds Courier Pros"
        )
        self.person = Person.objects.create(
            company=self.company,
            name="Shivam Singh",
            title="Logistics Specialist"
        )

    @patch("prospecting.views.router.generate")
    def test_sales_guidance_generation(self, mock_generate):
        mock_generate.return_value = {
            "type": "text",
            "text": (
                '{"talking_points": ["Save 20% fuel times"],'
                ' "recommended_angle": "Optimize routes to increase daily drops",'
                ' "recommended_next_step": "Send email pitching demo request",'
                ' "message_draft": "Hi Shivam, optimize your routes...",'
                ' "risks": ["high competitor density"],'
                ' "unknowns": ["exact fleet count"]}'
            )
        }

        url = reverse("lead-sales-guidance", kwargs={"pk": str(self.company.id)})
        payload = {
            "campaign_id": str(self.campaign.id),
            "person_id": str(self.person.id),
            "tone": "direct",
            "objective": "book_meeting"
        }
        res = self.client.post(url, data=payload)
        
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["recommended_angle"], "Optimize routes to increase daily drops")
        self.assertEqual(res.data["message_draft"], "Hi Shivam, optimize your routes...")
        
        # Verify persistence record in DB
        self.assertTrue(SalesGuidance.objects.filter(company=self.company, campaign=self.campaign).exists())


class EmailOutreachTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Email Campaign",
            product_description="some product",
            problem_statement="some problem",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds HVAC Ltd.",
            campaign=self.campaign
        )
        self.sequence = EmailSequence.objects.create(
            campaign=self.campaign,
            name="HVAC Sequence",
            steps=[]
        )
        self.message = EmailMessage.objects.create(
            sequence=self.sequence,
            company=self.company,
            recipient_email="ops@leedshvac.co.uk",
            subject="Optimize heating operations",
            body="Optimize your heating systems...",
            is_approved=False
        )

    def test_email_outreach_safety_checks(self):
        from prospecting.email.email_provider import MockEmailProvider
        provider = MockEmailProvider()

        # 1. Blocked: human approval pending
        success = provider.send(self.message)
        self.assertFalse(success)
        self.assertEqual(self.message.status, 'PENDING_APPROVAL')

        # 2. Blocked: unsubscribed recipient
        self.message.is_approved = True
        EmailUnsubscribe.objects.create(email="ops@leedshvac.co.uk")
        success = provider.send(self.message)
        self.assertFalse(success)
        self.assertEqual(self.message.status, 'CANCELLED')

        # 3. Blocked: bounced recipient
        EmailUnsubscribe.objects.all().delete()
        EmailBounce.objects.create(email="ops@leedshvac.co.uk")
        success = provider.send(self.message)
        self.assertFalse(success)
        self.assertEqual(self.message.status, 'FAILED')

        # 4. Successful: approved and not suppressed
        EmailBounce.objects.all().delete()
        success = provider.send(self.message)
        self.assertTrue(success)
        self.assertEqual(self.message.status, 'SENT')


class ReplyIntelligenceTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Reply Campaign",
            product_description="some product",
            problem_statement="some problem",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds HVAC Ltd.",
            campaign=self.campaign
        )
        self.sequence = EmailSequence.objects.create(
            campaign=self.campaign,
            name="HVAC Sequence",
            steps=[]
        )
        self.message = EmailMessage.objects.create(
            sequence=self.sequence,
            company=self.company,
            recipient_email="reply@leedshvac.co.uk",
            subject="Subject line",
            body="Hello...",
            is_approved=True,
            status='SENT'
        )

    @patch("prospecting.email.reply_classifier.router.generate")
    def test_reply_unsubscribe_compliance(self, mock_generate):
        mock_generate.return_value = {
            "type": "text",
            "text": '{"classification": "UNSUBSCRIBE", "confidence": 0.99, "reason": "Please unsubscribe me"}'
        }
        
        url = reverse("email-replies-list")
        payload = {
            "email_message_id": str(self.message.id),
            "reply_text": "Please remove me from your list."
        }
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["classification"], "UNSUBSCRIBE")
        
        # Verify auto-unsubscribe suppression is created
        self.assertTrue(EmailUnsubscribe.objects.filter(email="reply@leedshvac.co.uk").exists())

    @patch("prospecting.consumers.broadcast_campaign_event")
    @patch("prospecting.email.reply_classifier.router.generate")
    def test_reply_interested_broadcast(self, mock_generate, mock_broadcast):
        mock_generate.return_value = {
            "type": "text",
            "text": '{"classification": "INTERESTED", "confidence": 0.95, "reason": "Let book a meeting"}'
        }

        url = reverse("email-replies-list")
        payload = {
            "email_message_id": str(self.message.id),
            "reply_text": "Sure, I'd love to chat tomorrow."
        }
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["classification"], "INTERESTED")

        # Verify broadcast event triggered for POSITIVE_REPLY
        mock_broadcast.assert_called_once()
        self.assertEqual(mock_broadcast.call_args[1]["event_type"], "POSITIVE_REPLY")


class LeadFeedbackTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="HVAC Masters"
        )

    def test_submit_manual_feedback(self):
        url = reverse("lead-feedback", kwargs={"pk": str(self.company.id)})
        payload = {
            "feedback_type": "GOOD_SIGNAL",
            "notes": "Verified active hiring from careers portal."
        }
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["feedback_type"], "GOOD_SIGNAL")
        
        # Verify saved in DB
        self.assertTrue(LeadFeedback.objects.filter(company=self.company, feedback_type="GOOD_SIGNAL").exists())


class AnalyticsDashboardTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="HVAC Campaign",
            product_description="Route optimizer",
            problem_statement="delayed delivery",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds HVAC Pros",
            campaign=self.campaign
        )
        # Setup qualified lead
        self.company.analysis = WebsiteAnalysis.objects.create(
            company=self.company,
            lead_score=75
        )
        self.company.save()

        self.sequence = EmailSequence.objects.create(
            campaign=self.campaign,
            name="Funnel Sequence"
        )
        self.message = EmailMessage.objects.create(
            sequence=self.sequence,
            company=self.company,
            recipient_email="info@leedshvac.co.uk",
            subject="Hey",
            body="optimize...",
            status='SENT',
            sent_at=timezone.now()
        )
        self.reply = InboundReply.objects.create(
            email_message=self.message,
            reply_text="Yes, interested.",
            classification='INTERESTED'
        )

    def test_dashboard_overview_metrics(self):
        url = reverse("dashboard-overview")
        res = self.client.get(f"{url}?campaign_id={self.campaign.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["discovered"], 1)
        self.assertEqual(res.data["qualified"], 1)
        self.assertEqual(res.data["contacted"], 1)
        self.assertEqual(res.data["replied"], 1)
        self.assertEqual(res.data["positive"], 1)

    def test_dashboard_funnel_percentages(self):
        url = reverse("dashboard-funnel")
        res = self.client.get(f"{url}?campaign_id={self.campaign.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        stages = res.data["stages"]
        self.assertEqual(stages[0]["stage"], "Discovered")
        self.assertEqual(stages[1]["conversion"], 100.0) # 1/1 qualified
        self.assertEqual(stages[2]["conversion"], 100.0) # 1/1 contacted


class OpportunityTrendsTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="courier",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds Courier Express",
            category="Logistics",
            address="Leeds, West Yorkshire"
        )

    def test_geographical_opportunity_map(self):
        url = reverse("dashboard-opportunities")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["category"], "Logistics")
        self.assertEqual(res.data[0]["count"], 1)


class CeleryMonitoringTasksTestCase(TestCase):
    def test_periodic_beat_tasks_execution(self):
        from prospecting.tasks import (
            refresh_stale_companies_task, refresh_active_signals_task,
            recalculate_buying_windows_task, detect_new_signals_task
        )
        
        # Run synchronously
        res1 = refresh_stale_companies_task.delay()
        res2 = refresh_active_signals_task.delay()
        res3 = recalculate_buying_windows_task.delay()
        res4 = detect_new_signals_task.delay()
        
        self.assertIsNotNone(res1)
        self.assertIsNotNone(res2)
        self.assertIsNotNone(res3)
        self.assertIsNotNone(res4)


class CRMIntegrationTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="CRM Sync Campaign",
            product_description="some product",
            problem_statement="delayed timing",
            created_by=self.user
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="HVAC",
            location="Leeds"
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Leeds Sync HVAC",
            campaign=self.campaign
        )
        self.person = Person.objects.create(
            company=self.company,
            name="Shivam Singh",
            title="SDR"
        )

    def test_crm_sync_leads_and_contacts(self):
        url = reverse("lead-sync-crm", kwargs={"pk": str(self.company.id)})
        payload = {"owner_email": "shivam@nomad.ai"}
        
        # First sync
        res = self.client.post(url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)  # 1 Company record + 1 Contact record
        
        comp_record = [r for r in res.data if r["company"] is not None][0]
        self.assertTrue(comp_record["external_id"].startswith("crm-comp-"))

        # Try sync again, verify no duplicate CRM records are created
        res2 = self.client.post(url, data=payload)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res2.data), 2)
        comp_record_2 = [r for r in res2.data if r["company"] is not None][0]
        self.assertEqual(comp_record["external_id"], comp_record_2["external_id"])


class WorkspaceScopeTestCase(TestCase):
    def setUp(self):
        self.workspace = get_default_workspace()
        self.user = get_default_user()
        
        # Primary workspace lead company
        self.campaign_primary = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Primary campaign",
            created_by=self.user
        )
        self.run_primary = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign_primary,
            keyword="pest",
            location="Leeds"
        )
        self.company_primary = LeadCompany.objects.create(
            discovery_run=self.run_primary,
            name="Leeds Primary HVAC",
            campaign=self.campaign_primary
        )

        # Foreign workspace lead company
        self.other_workspace = Workspace.objects.create(name="Other Workspace")
        self.campaign_other = ProspectingCampaign.objects.create(
            workspace=self.other_workspace,
            name="Other campaign",
            created_by=self.user
        )
        self.run_other = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign_other,
            keyword="HVAC",
            location="Leeds"
        )
        self.company_other = LeadCompany.objects.create(
            discovery_run=self.run_other,
            name="Other Workspace HVAC",
            campaign=self.campaign_other
        )

    def test_leads_query_isolated_to_workspace(self):
        url = reverse("prospecting-leads")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        
        # Verify leads are filtered: only primary workspace company should be returned
        lead_names = [l["name"] for l in res.data["leads"]]
        self.assertIn("Leeds Primary HVAC", lead_names)
        self.assertNotIn("Other Workspace HVAC", lead_names)




