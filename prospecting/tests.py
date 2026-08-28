from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch, MagicMock
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from knowledge_base.models import UserProfile
from prospecting.exceptions import NormalizationError
from prospecting.models import (
    DiscoveryRun, LeadCompany, DiscoveryLead, LeadContact, WebsiteAnalysis,
    Workspace, ProspectingCampaign, CampaignLeadInsight, ICPProfile, ProblemSignal, Evidence, CompanySignal, Qualification,
    Person, ContactPoint, BuyingGroupMember, TargetList, CampaignEnrollment, SalesGuidance,
    EmailSequence, EmailMessage, EmailBounce, EmailUnsubscribe, InboundReply, LeadFeedback,
    ProspectingRequest, ProspectingSpecificationVersion
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
        self.assertEqual(queries[0], 'pest control Manchester, UK')
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
        self.assertTrue(discovery_provider_registry.has("apollo"))
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
        mock_contact_extractor.extract_contacts.assert_not_called()
        mock_website_analyzer.analyze_company.assert_not_called()

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


from django.test import override_settings

class DiscoveryLeadCappingTestCase(TestCase):
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
    def test_all_discovered_leads_saved_with_provenance(
        self, mock_broadcast_completion, mock_broadcast_progress, mock_contact_extractor,
        mock_website_analyzer, mock_execute
    ):
        mock_execute.return_value = type("ToolResult", (object,), {
            "success": True,
            "data": {
                "companies": [
                    {
                        "name": "Company One",
                        "website": "https://company1.co.uk",
                        "phone": "+441619999991",
                        "address": "Manchester, UK",
                        "category": "Pest Control",
                        "external_id": "test-company-1",
                        "raw_metadata": {}
                    },
                    {
                        "name": "Company Two",
                        "website": "https://company2.co.uk",
                        "phone": "+441619999992",
                        "address": "Manchester, UK",
                        "category": "Pest Control",
                        "external_id": "test-company-2",
                        "raw_metadata": {}
                    }
                ]
            }
        })()

        result = discover_campaign_async(str(self.run.id))
        self.assertEqual(result["status"], "success")
        # All discovered leads are saved without truncation
        self.assertEqual(result["leads_found"], 2)
        
        # Verify DiscoveryLead entries have source_provider set
        from prospecting.models import DiscoveryLead, CompanySource
        disc_leads = DiscoveryLead.objects.filter(discovery_run=self.run)
        self.assertEqual(disc_leads.count(), 2)
        for dl in disc_leads:
            self.assertEqual(dl.source_provider, "google_places")
            
        # Verify CompanySource records are created
        self.assertEqual(CompanySource.objects.filter(company__discovery_run=self.run).count(), 2)

        # Test the leads API endpoint directly with filters and facets
        from rest_framework.test import APIClient
        client = APIClient()
        url = f"/api/v3/prospecting/discovery-runs/{self.run.id}/leads/"
        response = client.get(url, {"sources": "google_places", "page": 1, "page_size": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 2)
        self.assertIn("google_places", response.data["available_sources"])
        self.assertIn("Pest Control", response.data["available_categories"])
        self.assertEqual(len(response.data["leads"]), 2)
        self.assertEqual(response.data["leads"][0]["source"], "google_places")


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
        self.company.category = "Web Search"
        self.company.save(update_fields=["category"])
        url = reverse("lead-intelligence", kwargs={"pk": str(self.company.id)})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("company", res.data)
        self.assertIn("scores", res.data)
        self.assertIn("explanation", res.data)
        self.assertIn("contacts", res.data)
        self.assertIn("buying_group", res.data)
        self.assertIsNone(res.data["scores"]["overall"])
        self.assertIsNone(res.data["scores"]["buying_window"])
        self.assertIsNone(res.data["freshness"]["last_researched"])
        self.assertEqual(res.data["contacts"][0]["name"], "Shivam Singh")
        self.assertEqual(res.data["company"]["category"], "HVAC")
        self.assertEqual(res.data["recommended_action"], "Research this account before outreach")

    def test_lead_intelligence_derives_values_from_stored_research(self):
        WebsiteAnalysis.objects.create(
            company=self.company,
            lead_score=8.4,
            lead_score_reason="The site advertises emergency call-out scheduling",
        )
        Evidence.objects.create(
            company=self.company,
            campaign=self.campaign,
            source_type="website",
            source_url="https://leedshvacpros.co.uk/services",
            source_title="Commercial maintenance services",
            evidence_text="Offers 24/7 commercial HVAC maintenance contracts.",
            confidence=0.9,
        )
        SalesGuidance.objects.create(
            company=self.company,
            campaign=self.campaign,
            talking_points=["Reference the 24/7 maintenance service."],
            recommended_angle="Service scheduling",
            recommended_next_step="Ask how call-outs are currently scheduled",
            message_draft="",
        )

        url = reverse("lead-intelligence", kwargs={"pk": str(self.company.id)})
        res = self.client.get(url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["scores"]["overall"], 84.0)
        self.assertEqual(res.data["scores"]["evidence_strength"], 65.0)
        self.assertEqual(res.data["source_summary"]["verifiable_sources"], 1)
        self.assertEqual(res.data["recommended_action"], "Ask how call-outs are currently scheduled")
        self.assertEqual(res.data["talking_points"], ["Reference the 24/7 maintenance service."])
        self.assertEqual(res.data["evidence_timeline"][0]["source_title"], "Commercial maintenance services")


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
            name="Leeds Courier Pros",
            enrichment_status="COMPLETED"
        )
        self.insight = CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign,
            qualification_status="COMPLETED",
            buying_group_status="COMPLETED"
        )
        self.person = Person.objects.create(
            company=self.company,
            name="Shivam Singh",
            title="Logistics Specialist"
        )

    @patch("prospecting.tasks.generate_sales_guidance_async.delay")
    def test_sales_guidance_generation_trigger(self, mock_delay):
        url = reverse("lead-sales-guidance", kwargs={"pk": str(self.company.id)})
        payload = {
            "campaign_id": str(self.campaign.id),
            "person_id": str(self.person.id),
            "tone": "direct",
            "objective": "book_meeting"
        }
        res = self.client.post(url, data=payload)
        
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["sales_guidance_status"], "QUEUED")
        mock_delay.assert_called_once_with(
            str(self.company.id),
            str(self.campaign.id),
            person_id=str(self.person.id),
            tone="direct",
            objective="book_meeting"
        )


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


class DecoupledDiscoveryBoundaryTestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            keyword="courier service",
            location="Leeds, UK"
        )

    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.WebsiteAnalyzer")
    @patch("prospecting.tasks.ContactExtractor")
    @patch("prospecting.tasks.broadcast_progress")
    @patch("prospecting.tasks.broadcast_completion")
    def test_discovery_boundary_decoupled_by_default(
        self, mock_broadcast_completion, mock_broadcast_progress, mock_contact_extractor,
        mock_website_analyzer, mock_execute
    ):
        """
        MODULE 1 TEST: Verify discovery completes after persisting LeadCompany and DiscoveryLead,
        and does NOT automatically call ContactExtractor or WebsiteAnalyzer.
        """
        mock_execute.return_value = type("ToolResult", (object,), {
            "success": True,
            "data": {
                "companies": [{
                    "name": "Leeds Courier Express",
                    "website": "https://leedscourier.example",
                    "phone": "+441130000000",
                    "address": "Leeds, UK",
                    "category": "Courier Services"
                }]
            }
        })()

        # Run decoupled discovery (enrich_leads=False by default)
        result = discover_campaign_async(str(self.run.id), enrich_leads=False)

        # 1. Discovery succeeds
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["leads_found"], 1)

        # 2. DiscoveryRun status reaches 'completed'
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "completed")

        # 3. LeadCompany and DiscoveryLead are persisted
        company = LeadCompany.objects.get(name="Leeds Courier Express")
        self.assertEqual(company.website, "https://leedscourier.example")
        self.assertTrue(DiscoveryLead.objects.filter(discovery_run=self.run, company=company).exists())

        # 4. Downstream contact extraction & website analysis were NOT called
        mock_contact_extractor.extract_contacts.assert_not_called()
        mock_website_analyzer.return_value.analyze_website.assert_not_called()

        # 5. Metrics survive
        self.assertEqual(result["new_leads"], 1)
        self.assertEqual(result["duplicates"], 0)

    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.WebsiteAnalyzer")
    @patch("prospecting.tasks.ContactExtractor")
    @patch("prospecting.tasks.broadcast_progress")
    @patch("prospecting.tasks.broadcast_completion")
    def test_legacy_enrich_leads_true_calls_downstream(
        self, mock_broadcast_completion, mock_broadcast_progress, mock_contact_extractor,
        mock_website_analyzer, mock_execute
    ):
        """
        Verify passing enrich_leads=True invokes downstream enrichment for backward compatibility.
        """
        mock_execute.return_value = type("ToolResult", (object,), {
            "success": True,
            "data": {
                "companies": [{
                    "name": "Leeds Courier Legacy",
                    "website": "https://legacycourier.example",
                    "phone": "+441131111111",
                    "address": "Leeds, UK",
                    "category": "Courier Services"
                }]
            }
        })()

        result = discover_campaign_async(str(self.run.id), enrich_leads=True)
        self.assertEqual(result["status"], "success")
        mock_contact_extractor.extract_contacts.assert_called_once()
        mock_website_analyzer.return_value.analyze_website.assert_called_once()


import uuid

class LeadContactEnrichmentAPITestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.company = LeadCompany.objects.create(
            name="Leeds Logistics Express",
            website="https://leedslogistics.example",
            phone="+441139998888",
            address="Leeds, UK",
            category="Logistics"
        )
        self.company_no_website = LeadCompany.objects.create(
            name="No Website Ltd",
            website="",
            phone="+441130001111",
            category="Services"
        )

    # 1. GET context for valid lead
    def test_get_enrich_context_valid_lead(self):
        url = reverse("lead-enrich-context", kwargs={"pk": self.company.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["lead_id"], str(self.company.id))
        self.assertEqual(res.data["company_name"], "Leeds Logistics Express")
        self.assertEqual(res.data["website"], "https://leedslogistics.example")
        self.assertTrue(res.data["is_website_usable"])
        self.assertEqual(res.data["current_contact_count"], 0)
        self.assertEqual(res.data["enrichment_status"], "NOT_STARTED")
        self.assertTrue(res.data["can_enrich"])
        self.assertIsNone(res.data["reason"])

    # 2. GET context for lead without website
    def test_get_enrich_context_no_website(self):
        url = reverse("lead-enrich-context", kwargs={"pk": self.company_no_website.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["is_website_usable"])
        self.assertFalse(res.data["can_enrich"])
        self.assertEqual(res.data["reason"], "Lead company website is required for contact enrichment.")

    # 3. GET context for nonexistent lead
    def test_get_enrich_context_nonexistent_lead(self):
        url = reverse("lead-enrich-context", kwargs={"pk": uuid.uuid4()})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 4. POST enrichment for valid lead
    @patch("prospecting.tasks.enrich_lead_contacts_async.delay")
    def test_post_enrichment_valid_lead(self, mock_delay):
        url = reverse("lead-enrich", kwargs={"pk": self.company.id})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["enrichment_status"], "QUEUED")
        self.company.refresh_from_db()
        self.assertEqual(self.company.enrichment_status, "QUEUED")
        mock_delay.assert_called_once_with(str(self.company.id))

    # 5. POST enrichment without website
    def test_post_enrichment_no_website(self):
        url = reverse("lead-enrich", kwargs={"pk": self.company_no_website.id})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "INVALID_LEAD_WEBSITE")
        self.assertEqual(res.data["message"], "Lead company website is required for contact enrichment.")

    # 6. POST enrichment for nonexistent lead
    def test_post_enrichment_nonexistent_lead(self):
        url = reverse("lead-enrich", kwargs={"pk": uuid.uuid4()})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 7 & 8 & 13. POST enrichment when already QUEUED or RUNNING (Conflict / Idempotency)
    @patch("prospecting.tasks.enrich_lead_contacts_async.delay")
    def test_post_enrichment_already_queued_or_running(self, mock_delay):
        self.company.enrichment_status = 'QUEUED'
        self.company.save()

        url = reverse("lead-enrich", kwargs={"pk": self.company.id})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data["error"], "ENRICHMENT_ALREADY_IN_PROGRESS")
        mock_delay.assert_not_called()

        self.company.enrichment_status = 'RUNNING'
        self.company.save()
        res2 = self.client.post(url)
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        mock_delay.assert_not_called()

    # 9, 10, 14, 15, 16. Celery task execution success -> COMPLETED
    @patch("prospecting.tasks.WebsiteAnalyzer")
    @patch("prospecting.tasks.ContactExtractor.extract_contacts")
    def test_enrich_lead_contacts_async_success(self, mock_extract, mock_website_analyzer):
        from prospecting.tasks import enrich_lead_contacts_async
        mock_extract.return_value = [
            LeadContact(company=self.company, email="ops@leedslogistics.example")
        ]

        result = enrich_lead_contacts_async(str(self.company.id))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["contacts_count"], 1)
        self.company.refresh_from_db()
        self.assertEqual(self.company.enrichment_status, "COMPLETED")
        self.assertEqual(self.company.enrichment_error, {})

        mock_extract.assert_called_once()
        mock_website_analyzer.assert_not_called()

    # 11. ContactExtractor failure -> FAILED
    @patch("prospecting.tasks.ContactExtractor.extract_contacts")
    def test_enrich_lead_contacts_async_failure(self, mock_extract):
        from prospecting.tasks import enrich_lead_contacts_async
        mock_extract.side_effect = Exception("Crawl connection timeout")

        with self.assertRaises(Exception):
            enrich_lead_contacts_async(str(self.company.id))

        self.company.refresh_from_db()
        self.assertEqual(self.company.enrichment_status, "FAILED")
        self.assertEqual(self.company.enrichment_error["error"], "Crawl connection timeout")

    # 12. Failed enrichment can be retried
    @patch("prospecting.tasks.enrich_lead_contacts_async.delay")
    def test_failed_enrichment_can_be_retried(self, mock_delay):
        self.company.enrichment_status = 'FAILED'
        self.company.enrichment_error = {"error": "Previous crash"}
        self.company.save()

        url = reverse("lead-enrich", kwargs={"pk": self.company.id})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["enrichment_status"], "QUEUED")
        self.company.refresh_from_db()
        self.assertEqual(self.company.enrichment_status, "QUEUED")
        mock_delay.assert_called_once_with(str(self.company.id))


class LeadQualificationAPITestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.workspace = getattr(self.user, 'personal_workspace', None) or Workspace.objects.first()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Route Optimization Campaign",
            product_description="SaaS route optimization software",
            problem_statement="High fuel costs and inefficient delivery routing",
            status="ACTIVE",
            created_by=self.user
        )
        self.company = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Leeds Express Freight",
            website="https://leedsexfreight.example",
            phone="+441135556666",
            address="Leeds, UK",
            category="Freight",
            enrichment_status="COMPLETED"
        )
        self.company_un_enriched = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Un-Enriched Haulage",
            website="https://unenrichedhaulage.example",
            category="Haulage",
            enrichment_status="NOT_STARTED"
        )
        self.company_no_website = LeadCompany.objects.create(
            campaign=self.campaign,
            name="No Website Express",
            website="",
            category="Express",
            enrichment_status="COMPLETED"
        )

    # 1. GET qualification context for valid lead
    def test_get_qualify_context_valid_lead(self):
        url = reverse("lead-qualify-context", kwargs={"pk": self.company.id})
        res = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["lead_id"], str(self.company.id))
        self.assertEqual(res.data["company_name"], "Leeds Express Freight")
        self.assertEqual(res.data["campaign_id"], str(self.campaign.id))
        self.assertTrue(res.data["can_qualify"])
        self.assertIsNone(res.data["reason"])
        self.assertEqual(res.data["current_qualification_status"], "NOT_STARTED")

    # 2. GET context when prerequisites are missing (enrichment != COMPLETED)
    def test_get_qualify_context_enrichment_not_ready(self):
        url = reverse("lead-qualify-context", kwargs={"pk": self.company_un_enriched.id})
        res = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["can_qualify"])
        self.assertEqual(res.data["reason"], "Lead contact enrichment must be completed before qualification.")

    # 3. GET context for nonexistent lead
    def test_get_qualify_context_nonexistent_lead(self):
        url = reverse("lead-qualify-context", kwargs={"pk": uuid.uuid4()})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 4. POST qualification for valid lead
    @patch("prospecting.tasks.qualify_lead_async.delay")
    def test_post_qualification_valid_lead(self, mock_delay):
        url = reverse("lead-qualify", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["qualification_status"], "QUEUED")
        insight = CampaignLeadInsight.objects.get(company=self.company, campaign=self.campaign)
        self.assertEqual(insight.qualification_status, "QUEUED")
        mock_delay.assert_called_once_with(str(self.company.id), str(self.campaign.id))

    # 5. POST qualification when prerequisites missing (ENRICHMENT_NOT_READY)
    def test_post_qualification_enrichment_not_ready(self):
        url = reverse("lead-qualify", kwargs={"pk": self.company_un_enriched.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "ENRICHMENT_NOT_READY")

    # 6. POST qualification for nonexistent lead
    def test_post_qualification_nonexistent_lead(self):
        url = reverse("lead-qualify", kwargs={"pk": uuid.uuid4()})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 7 & 8 & 13. POST when qualification already QUEUED or RUNNING
    @patch("prospecting.tasks.qualify_lead_async.delay")
    def test_post_qualification_already_queued_or_running(self, mock_delay):
        insight, _ = CampaignLeadInsight.objects.get_or_create(company=self.company, campaign=self.campaign)
        insight.qualification_status = 'QUEUED'
        insight.save()

        url = reverse("lead-qualify", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data["error"], "QUALIFICATION_ALREADY_IN_PROGRESS")
        mock_delay.assert_not_called()

        insight.qualification_status = 'RUNNING'
        insight.save()
        res2 = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        mock_delay.assert_not_called()

    # 9, 10, 14, 15, 16, 17. Celery task execution -> WebsiteAnalyzer -> COMPLETED
    @patch("prospecting.tasks.WebsiteAnalyzer.analyze_website")
    @patch("prospecting.qualification.buying_group.BuyingGroupWorkflow")
    def test_qualify_lead_async_success(self, mock_buying_group, mock_analyze):
        from prospecting.tasks import qualify_lead_async

        mock_analyze.return_value = type("AnalysisResult", (object,), {
            "lead_score": 85.0
        })()

        result = qualify_lead_async(str(self.company.id), str(self.campaign.id))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["qualification_status"], "COMPLETED")
        self.assertEqual(result["lead_score"], 85.0)

        insight = CampaignLeadInsight.objects.get(company=self.company, campaign=self.campaign)
        self.assertEqual(insight.qualification_status, "COMPLETED")
        self.assertEqual(insight.qualification_error, {})

        mock_analyze.assert_called_once()
        mock_buying_group.assert_not_called()

    # 11. WebsiteAnalyzer failure -> FAILED
    @patch("prospecting.tasks.WebsiteAnalyzer.analyze_website")
    def test_qualify_lead_async_failure(self, mock_analyze):
        from prospecting.tasks import qualify_lead_async
        mock_analyze.side_effect = Exception("LLM router timeout")

        with self.assertRaises(Exception):
            qualify_lead_async(str(self.company.id), str(self.campaign.id))

        insight = CampaignLeadInsight.objects.get(company=self.company, campaign=self.campaign)
        self.assertEqual(insight.qualification_status, "FAILED")
        self.assertEqual(insight.qualification_error["error"], "LLM router timeout")

    # 12. FAILED qualification can be retried
    @patch("prospecting.tasks.qualify_lead_async.delay")
    def test_failed_qualification_can_be_retried(self, mock_delay):
        insight, _ = CampaignLeadInsight.objects.get_or_create(company=self.company, campaign=self.campaign)
        insight.qualification_status = 'FAILED'
        insight.qualification_error = {"error": "Previous crash"}
        insight.save()

        url = reverse("lead-qualify", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["qualification_status"], "QUEUED")
        insight.refresh_from_db()
        self.assertEqual(insight.qualification_status, "QUEUED")
        mock_delay.assert_called_once_with(str(self.company.id), str(self.campaign.id))


class LeadBuyingGroupAPITestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.workspace = getattr(self.user, 'personal_workspace', None) or Workspace.objects.first()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Route Optimization Campaign",
            product_description="SaaS route optimization software",
            problem_statement="High fuel costs and inefficient delivery routing",
            status="ACTIVE",
            created_by=self.user
        )
        self.campaign_other = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Fleet Telematics Campaign",
            product_description="Fleet telematics and hardware",
            status="ACTIVE",
            created_by=self.user
        )
        self.company = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Leeds Express Freight",
            website="https://leedsexfreight.example",
            phone="+441135556666",
            address="Leeds, UK",
            category="Freight",
            enrichment_status="COMPLETED"
        )
        self.insight = CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign,
            qualification_status="COMPLETED",
            fit_score=88.0,
            fit_level="HIGH",
            company_summary="Leading road freight operator in West Yorkshire."
        )

        self.company_unqualified = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Unqualified Logistics",
            website="https://unqualifiedlogistics.example",
            enrichment_status="COMPLETED"
        )
        self.insight_unqualified = CampaignLeadInsight.objects.create(
            company=self.company_unqualified,
            campaign=self.campaign,
            qualification_status="NOT_STARTED"
        )

    # 1. GET buying-group context for valid lead
    def test_get_buying_group_context_valid_lead(self):
        url = reverse("lead-buying-group-context", kwargs={"pk": self.company.id})
        res = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["lead_id"], str(self.company.id))
        self.assertEqual(res.data["company_name"], "Leeds Express Freight")
        self.assertEqual(res.data["campaign_id"], str(self.campaign.id))
        self.assertEqual(res.data["qualification_status"], "COMPLETED")
        self.assertEqual(res.data["buying_group_status"], "NOT_STARTED")
        self.assertTrue(res.data["can_run"])
        self.assertIsNone(res.data["reason"])
        self.assertEqual(res.data["qualification_context"]["fit_score"], 88.0)
        self.assertEqual(res.data["qualification_context"]["fit_level"], "HIGH")

    # 2. GET context when qualification is not completed
    def test_get_buying_group_context_qualification_not_ready(self):
        url = reverse("lead-buying-group-context", kwargs={"pk": self.company_unqualified.id})
        res = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["can_run"])
        self.assertEqual(res.data["reason"], "Lead qualification must be completed before buying group identification.")

    # 3. GET context for nonexistent lead
    def test_get_buying_group_context_nonexistent_lead(self):
        url = reverse("lead-buying-group-context", kwargs={"pk": uuid.uuid4()})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 4. POST buying-group with valid prerequisites
    @patch("prospecting.tasks.identify_buying_group_async.delay")
    def test_post_buying_group_valid_lead(self, mock_delay):
        url = reverse("lead-buying-group", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["buying_group_status"], "QUEUED")
        self.insight.refresh_from_db()
        self.assertEqual(self.insight.buying_group_status, "QUEUED")
        mock_delay.assert_called_once_with(str(self.company.id), str(self.campaign.id))

    # 5, 6. POST when qualification is NOT_STARTED, QUEUED, RUNNING, or FAILED
    def test_post_buying_group_qualification_not_ready(self):
        for invalid_status in ["NOT_STARTED", "QUEUED", "RUNNING", "FAILED"]:
            self.insight_unqualified.qualification_status = invalid_status
            self.insight_unqualified.save()

            url = reverse("lead-buying-group", kwargs={"pk": self.company_unqualified.id})
            res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"], "QUALIFICATION_NOT_READY")

    # 7 & 8 & 14. POST when buying group is already QUEUED or RUNNING (Conflict / Idempotency)
    @patch("prospecting.tasks.identify_buying_group_async.delay")
    def test_post_buying_group_already_queued_or_running(self, mock_delay):
        self.insight.buying_group_status = 'QUEUED'
        self.insight.save()

        url = reverse("lead-buying-group", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data["error"], "BUYING_GROUP_ALREADY_IN_PROGRESS")
        mock_delay.assert_not_called()

        self.insight.buying_group_status = 'RUNNING'
        self.insight.save()
        res2 = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        mock_delay.assert_not_called()

    # 9, 15, 16, 17. Successful Celery execution -> BuyingGroupWorkflow -> COMPLETED
    @patch("prospecting.models.SalesGuidance")
    @patch("prospecting.qualification.buying_group.BuyingGroupWorkflow.run")
    def test_identify_buying_group_async_success(self, mock_run, mock_sales_guidance):
        from prospecting.tasks import identify_buying_group_async

        person = Person.objects.create(company=self.company, name="Sarah Jenkins", title="Head of Operations")
        mock_member = BuyingGroupMember.objects.create(
            campaign=self.campaign,
            company=self.company,
            person=person,
            role_type="DECISION_MAKER",
            relevance_score=90
        )
        mock_run.return_value = [mock_member]

        result = identify_buying_group_async(str(self.company.id), str(self.campaign.id))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["buying_group_status"], "COMPLETED")
        self.assertEqual(result["members_count"], 1)

        self.insight.refresh_from_db()
        self.assertEqual(self.insight.buying_group_status, "COMPLETED")
        self.assertEqual(self.insight.buying_group_error, {})

        mock_run.assert_called_once()
        mock_sales_guidance.assert_not_called()

    # 10. Failed Celery execution
    @patch("prospecting.qualification.buying_group.BuyingGroupWorkflow.run")
    def test_identify_buying_group_async_failure(self, mock_run):
        from prospecting.tasks import identify_buying_group_async
        mock_run.side_effect = Exception("LLM schema parsing failed")

        with self.assertRaises(Exception):
            identify_buying_group_async(str(self.company.id), str(self.campaign.id))

        self.insight.refresh_from_db()
        self.assertEqual(self.insight.buying_group_status, "FAILED")
        self.assertEqual(self.insight.buying_group_error["error"], "LLM schema parsing failed")

    # 11. FAILED buying group can be retried
    @patch("prospecting.tasks.identify_buying_group_async.delay")
    def test_failed_buying_group_can_be_retried(self, mock_delay):
        self.insight.buying_group_status = 'FAILED'
        self.insight.buying_group_error = {"error": "Previous crash"}
        self.insight.save()

        url = reverse("lead-buying-group", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["buying_group_status"], "QUEUED")
        self.insight.refresh_from_db()
        self.assertEqual(self.insight.buying_group_status, "QUEUED")
        mock_delay.assert_called_once_with(str(self.company.id), str(self.campaign.id))

    # 13, 18. Campaign isolation for buying group state
    def test_campaign_isolation_for_buying_group_state(self):
        insight_other = CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign_other,
            qualification_status="NOT_STARTED",
            buying_group_status="NOT_STARTED"
        )

        url = reverse("lead-buying-group-context", kwargs={"pk": self.company.id})
        res_primary = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertTrue(res_primary.data["can_run"])

        res_other = self.client.get(url, {"campaign_id": str(self.campaign_other.id)})
        self.assertFalse(res_other.data["can_run"])
        self.assertEqual(res_other.data["reason"], "Lead qualification must be completed before buying group identification.")


class LeadSalesGuidanceAPITestCase(TestCase):
    def setUp(self):
        self.user = get_default_user()
        self.workspace = getattr(self.user, 'personal_workspace', None) or Workspace.objects.first()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Route Optimization Campaign",
            product_description="SaaS route optimization software",
            problem_statement="High fuel costs and inefficient delivery routing",
            status="ACTIVE",
            created_by=self.user
        )
        self.campaign_other = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Fleet Telematics Campaign",
            product_description="Fleet telematics and hardware",
            status="ACTIVE",
            created_by=self.user
        )
        self.company = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Leeds Express Freight",
            website="https://leedsexfreight.example",
            phone="+441135556666",
            address="Leeds, UK",
            category="Freight",
            enrichment_status="COMPLETED"
        )
        self.person = Person.objects.create(
            company=self.company,
            name="Sarah Jenkins",
            title="Head of Operations"
        )
        self.insight = CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign,
            qualification_status="COMPLETED",
            buying_group_status="COMPLETED",
            sales_guidance_status="NOT_STARTED"
        )
        self.member = BuyingGroupMember.objects.create(
            campaign=self.campaign,
            company=self.company,
            person=self.person,
            role_type="DECISION_MAKER",
            relevance_score=90
        )
        self.evidence = Evidence.objects.create(
            company=self.company,
            source_type="WEBSITE",
            source_url="https://leedsexfreight.example/tech",
            evidence_text="Uses legacy dispatch routing spreadsheets",
            confidence=0.9
        )

        self.company_unqualified = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Unqualified Logistics",
            website="https://unqualifiedlogistics.example",
            enrichment_status="COMPLETED"
        )
        self.insight_unqualified = CampaignLeadInsight.objects.create(
            company=self.company_unqualified,
            campaign=self.campaign,
            qualification_status="COMPLETED",
            buying_group_status="NOT_STARTED"
        )

    # 1. GET context when buying group is complete
    def test_get_sales_guidance_context_valid_lead(self):
        url = reverse("lead-sales-guidance-context", kwargs={"pk": self.company.id})
        res = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["lead_id"], str(self.company.id))
        self.assertEqual(res.data["company_name"], "Leeds Express Freight")
        self.assertEqual(res.data["campaign_id"], str(self.campaign.id))
        self.assertEqual(res.data["buying_group_status"], "COMPLETED")
        self.assertEqual(res.data["sales_guidance_status"], "NOT_STARTED")
        self.assertTrue(res.data["can_run"])
        self.assertIsNone(res.data["reason"])
        self.assertEqual(res.data["prompt_context"]["contact_name"], "Sarah Jenkins")
        self.assertEqual(res.data["prompt_context"]["evidence_count"], 1)

    # 2. GET context when buying group is incomplete
    def test_get_sales_guidance_context_buying_group_not_ready(self):
        url = reverse("lead-sales-guidance-context", kwargs={"pk": self.company_unqualified.id})
        res = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["can_run"])
        self.assertEqual(res.data["reason"], "Buying group analysis must be completed before generating sales guidance.")

    # 3. GET context for nonexistent lead
    def test_get_sales_guidance_context_nonexistent_lead(self):
        url = reverse("lead-sales-guidance-context", kwargs={"pk": uuid.uuid4()})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # 4. POST sales guidance with valid prerequisites
    @patch("prospecting.tasks.generate_sales_guidance_async.delay")
    def test_post_sales_guidance_valid_lead(self, mock_delay):
        url = reverse("lead-sales-guidance", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["sales_guidance_status"], "QUEUED")
        self.insight.refresh_from_db()
        self.assertEqual(self.insight.sales_guidance_status, "QUEUED")
        mock_delay.assert_called_once_with(
            str(self.company.id),
            str(self.campaign.id),
            person_id=None,
            tone="professional",
            objective="book_meeting"
        )

    # 5. POST when buying group is not completed (prerequisite check)
    def test_post_sales_guidance_buying_group_not_ready(self):
        for invalid_status in ["NOT_STARTED", "QUEUED", "RUNNING", "FAILED"]:
            self.insight_unqualified.buying_group_status = invalid_status
            self.insight_unqualified.save()

            url = reverse("lead-sales-guidance", kwargs={"pk": self.company_unqualified.id})
            res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(res.data["error"], "BUYING_GROUP_NOT_READY")

    # 6. POST when sales guidance is already QUEUED or RUNNING (conflict check)
    @patch("prospecting.tasks.generate_sales_guidance_async.delay")
    def test_post_sales_guidance_already_queued_or_running(self, mock_delay):
        self.insight.sales_guidance_status = 'QUEUED'
        self.insight.save()

        url = reverse("lead-sales-guidance", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data["error"], "SALES_GUIDANCE_ALREADY_IN_PROGRESS")
        mock_delay.assert_not_called()

        self.insight.sales_guidance_status = 'RUNNING'
        self.insight.save()
        res2 = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        mock_delay.assert_not_called()

    # 7. Successful Celery execution
    @patch("llm.router.IntelligentRouter.generate")
    def test_generate_sales_guidance_async_success(self, mock_generate):
        from prospecting.tasks import generate_sales_guidance_async
        mock_generate.return_value = {
            "type": "text",
            "text": (
                '{"talking_points": ["Cut dispatch scheduling time by 50%"],'
                ' "recommended_angle": "Focus on replacing spreadsheet routing",'
                ' "recommended_next_step": "Offer a 15-minute workflow audit",'
                ' "message_draft": "Hi Sarah, saw you handle fleet logistics at Leeds Express Freight...",'
                ' "risks": ["Driver pushback on app adoption"],'
                ' "unknowns": ["Current TMS contract length"]}'
            )
        }

        result = generate_sales_guidance_async(
            str(self.company.id),
            str(self.campaign.id),
            person_id=str(self.person.id),
            tone="direct",
            objective="demo"
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sales_guidance_status"], "COMPLETED")

        self.insight.refresh_from_db()
        self.assertEqual(self.insight.sales_guidance_status, "COMPLETED")
        self.assertEqual(self.insight.sales_guidance_error, {})

        guidance = SalesGuidance.objects.filter(company=self.company, campaign=self.campaign).first()
        self.assertIsNotNone(guidance)
        self.assertEqual(guidance.recommended_angle, "Focus on replacing spreadsheet routing")
        self.assertEqual(guidance.message_draft, "Hi Sarah, saw you handle fleet logistics at Leeds Express Freight...")

    # 8. Failed Celery execution
    @patch("llm.router.IntelligentRouter.generate")
    def test_generate_sales_guidance_async_failure(self, mock_generate):
        from prospecting.tasks import generate_sales_guidance_async
        mock_generate.side_effect = Exception("LLM rate limit reached")

        with self.assertRaises(Exception):
            generate_sales_guidance_async(str(self.company.id), str(self.campaign.id))

        self.insight.refresh_from_db()
        self.assertEqual(self.insight.sales_guidance_status, "FAILED")
        self.assertEqual(self.insight.sales_guidance_error["error"], "LLM rate limit reached")

    # 9. FAILED status can be retried
    @patch("prospecting.tasks.generate_sales_guidance_async.delay")
    def test_failed_sales_guidance_can_be_retried(self, mock_delay):
        self.insight.sales_guidance_status = 'FAILED'
        self.insight.sales_guidance_error = {"error": "Previous crash"}
        self.insight.save()

        url = reverse("lead-sales-guidance", kwargs={"pk": self.company.id})
        res = self.client.post(url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["sales_guidance_status"], "QUEUED")
        self.insight.refresh_from_db()
        self.assertEqual(self.insight.sales_guidance_status, "QUEUED")
        mock_delay.assert_called_once()

    # 10. Campaign isolation
    def test_campaign_isolation_for_sales_guidance_state(self):
        insight_other = CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign_other,
            qualification_status="COMPLETED",
            buying_group_status="NOT_STARTED",
            sales_guidance_status="NOT_STARTED"
        )

        url = reverse("lead-sales-guidance-context", kwargs={"pk": self.company.id})
        res_primary = self.client.get(url, {"campaign_id": str(self.campaign.id)})
        self.assertTrue(res_primary.data["can_run"])

        res_other = self.client.get(url, {"campaign_id": str(self.campaign_other.id)})
        self.assertFalse(res_other.data["can_run"])
        self.assertEqual(res_other.data["reason"], "Buying group analysis must be completed before generating sales guidance.")

    # 11. No automatic cascade test
    @patch("prospecting.tasks.generate_sales_guidance_async.delay")
    @patch("prospecting.tasks.identify_buying_group_async.delay")
    @patch("prospecting.tasks.qualify_lead_async.delay")
    @patch("prospecting.tasks.enrich_lead_contacts_async.delay")
    def test_no_automatic_cascade_to_sales_guidance(self, mock_enrich, mock_qualify, mock_bg, mock_sg):
        # 1. Triggering Contact Enrichment only dispatches enrich task, never sales guidance
        enrich_url = reverse("lead-enrich", kwargs={"pk": self.company.id})
        res_enrich = self.client.post(enrich_url)
        self.assertEqual(res_enrich.status_code, status.HTTP_202_ACCEPTED)
        mock_enrich.assert_called_once()
        mock_sg.assert_not_called()

        # 2. Reset enrichment status to COMPLETED for qualify test
        self.company.enrichment_status = 'COMPLETED'
        self.company.save(update_fields=['enrichment_status'])

        # Triggering Lead Qualification only dispatches qualify task, never sales guidance
        qual_url = reverse("lead-qualify", kwargs={"pk": self.company.id})
        res_qual = self.client.post(qual_url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res_qual.status_code, status.HTTP_202_ACCEPTED)
        mock_qualify.assert_called_once()
        mock_sg.assert_not_called()

        # 3. Ensure qualification status is COMPLETED for buying group test
        self.insight.qualification_status = 'COMPLETED'
        self.insight.save(update_fields=['qualification_status'])

        # Triggering Buying Group only dispatches buying group task, never sales guidance
        bg_url = reverse("lead-buying-group", kwargs={"pk": self.company.id})
        res_bg = self.client.post(bg_url, {"campaign_id": str(self.campaign.id)})
        self.assertEqual(res_bg.status_code, status.HTTP_202_ACCEPTED)
        mock_bg.assert_called_once()
        mock_sg.assert_not_called()


class DiscoverMoreLeadsAPITestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = get_default_user()
        self.workspace = get_default_workspace()
        self.other_workspace = Workspace.objects.create(name="Other Workspace")

        self.req = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective="Find logistics companies in Leeds",
            raw_target="Logistics and freight companies",
            status="CONFIRMED"
        )
        self.spec_version = ProspectingSpecificationVersion.objects.create(
            request=self.req,
            version=1,
            status="CONFIRMED",
            specification_json={
                "objective": {"value": "Find logistics companies", "provenance": "EXPLICIT_USER"},
                "target": {
                    "description": {"value": "Freight & transport", "provenance": "EXPLICIT_USER"},
                    "categories": {"value": ["Freight Forwarding", "Courier Delivery"], "provenance": "EXPLICIT_USER"}
                },
                "geography": {
                    "cities": {"value": ["Leeds"], "provenance": "EXPLICIT_USER"},
                    "countries": {"value": ["UK"], "provenance": "EXPLICIT_USER"}
                }
            }
        )
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Leeds Logistics Campaign",
            product_description="Fleet routing SaaS",
            problem_statement="High fuel costs",
            geography={"location": "Leeds, UK"},
            status="ACTIVE",
            created_by=self.user,
            prospecting_request=self.req
        )
        self.initial_run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign,
            prospecting_request=self.req,
            specification_version=self.spec_version,
            keyword="Freight Forwarding",
            location="Leeds, UK",
            status="completed",
            total_leads_found=2
        )
        # Initial campaign lead
        self.existing_company = LeadCompany.objects.create(
            discovery_run=self.initial_run,
            campaign=self.campaign,
            name="Alpha Logistics Ltd",
            website="https://alphalogistics.example",
            phone="0113111222",
            address="10 Briggate, Leeds",
            category="Freight",
            enrichment_status="NOT_STARTED"
        )
        DiscoveryLead.objects.create(discovery_run=self.initial_run, company=self.existing_company)

    # 1. Valid discover-more request
    @patch("prospecting.tasks.discover_more_leads_async.delay")
    def test_discover_more_valid_request(self, mock_delay):
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": self.campaign.id})
        res = self.client.post(url, {"limit": 10})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["status"], "queued")
        self.assertEqual(res.data["requested_limit"], 10)
        self.assertEqual(res.data["campaign_id"], str(self.campaign.id))
        self.assertTrue("run_id" in res.data)

        run = DiscoveryRun.objects.get(id=res.data["run_id"])
        self.assertEqual(run.campaign, self.campaign)
        self.assertEqual(run.status, "queued")
        mock_delay.assert_called_once_with(str(run.id), batch_size=10)

    # 2. Default batch size when limit is omitted
    @patch("prospecting.tasks.discover_more_leads_async.delay")
    def test_discover_more_default_batch_size(self, mock_delay):
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": self.campaign.id})
        res = self.client.post(url, {})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["requested_limit"], 10)
        mock_delay.assert_called_once()

    # 3. Custom batch size
    @patch("prospecting.tasks.discover_more_leads_async.delay")
    def test_discover_more_custom_batch_size(self, mock_delay):
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": self.campaign.id})
        res = self.client.post(url, {"limit": 5})
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["requested_limit"], 5)
        run_id = res.data["run_id"]
        mock_delay.assert_called_once_with(run_id, batch_size=5)

    # 4. Campaign authorization (wrong workspace)
    def test_discover_more_campaign_authorization(self):
        other_campaign = ProspectingCampaign.objects.create(
            workspace=self.other_workspace,
            name="Other Workspace Campaign",
            product_description="Something",
            problem_statement="Something",
            created_by=self.user
        )
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": other_campaign.id})
        res = self.client.post(url, {"limit": 10})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["error"], "CAMPAIGN_NOT_FOUND")

    # 5. Missing campaign
    def test_discover_more_missing_campaign(self):
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": uuid.uuid4()})
        res = self.client.post(url, {"limit": 10})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["error"], "CAMPAIGN_NOT_FOUND")

    # 6. Missing specification
    def test_discover_more_missing_specification(self):
        empty_req_campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="Empty Spec Campaign",
            product_description="",
            problem_statement="",
            created_by=self.user,
            prospecting_request=None
        )
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": empty_req_campaign.id})
        res = self.client.post(url, {"limit": 10})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"], "SPECIFICATION_NOT_READY")

    # 7. Invalid batch size
    def test_discover_more_invalid_batch_size(self):
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": self.campaign.id})
        res1 = self.client.post(url, {"limit": 0})
        self.assertEqual(res1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res1.data["error"], "INVALID_BATCH_SIZE")

        res2 = self.client.post(url, {"limit": -10})
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res2.data["error"], "INVALID_BATCH_SIZE")

        res3 = self.client.post(url, {"limit": "invalid"})
        self.assertEqual(res3.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res3.data["error"], "INVALID_BATCH_SIZE")

    # 8. Deduplicates existing campaign leads
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_deduplicates_existing_campaign_leads(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        # Mock tool returning the existing lead + 2 new leads
        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Alpha Logistics Ltd", "website": "https://alphalogistics.example", "phone": "0113111222"},
                    {"name": "Beta Haulage", "website": "https://betahaulage.example", "phone": "0113222333"},
                    {"name": "Gamma Express", "website": "https://gammaexpress.example", "phone": "0113333444"}
                ]
            }
        )

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["leads_created"], 2)  # Alpha Logistics was skipped!

        company_names = list(LeadCompany.objects.filter(campaign=self.campaign).values_list('name', flat=True))
        self.assertIn("Beta Haulage", company_names)
        self.assertIn("Gamma Express", company_names)
        self.assertEqual(company_names.count("Alpha Logistics Ltd"), 1)

    # 9. Does not create duplicate DiscoveryLead records
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_does_not_create_duplicate_discovery_leads(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Delta Transit", "website": "https://deltatransit.example", "phone": "0113444555"}
                ]
            }
        )

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        run_id = result["run_id"]
        lead = LeadCompany.objects.get(name="Delta Transit")
        dl_count = DiscoveryLead.objects.filter(discovery_run_id=run_id, company=lead).count()
        self.assertEqual(dl_count, 1)

    # 10. Respects batch limit
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_respects_batch_limit(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": f"Company {i}", "website": f"https://company{i}.example", "phone": f"0113000{i}"}
                    for i in range(10)
                ]
            }
        )

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=3)
        self.assertEqual(result["leads_created"], 3)
        self.assertEqual(result["requested_limit"], 3)
        self.assertFalse(result["exhausted"])

    # 11. Returns fewer when search space is exhausted
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_returns_fewer_when_search_space_exhausted(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Epsilon Movers", "website": "https://epsilonmovers.example", "phone": "0113999888"}
                ]
            }
        )

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=5)
        self.assertEqual(result["leads_created"], 1)
        self.assertEqual(result["requested_limit"], 5)
        self.assertTrue(result["exhausted"])

    # 12. Records exhaustion
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_records_exhaustion(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(success=True, tool_name="search_companies", data={"companies": []})

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        self.assertEqual(result["leads_created"], 0)
        self.assertTrue(result["exhausted"])

    # 13. Tracks discovery run lifecycle
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_tracks_discovery_run(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Zeta Cargo", "website": "https://zetacargo.example", "phone": "0113777666"}
                ]
            }
        )

        run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign,
            keyword="Courier",
            location="Leeds, UK",
            status="running"
        )
        result = DiscoveryBatchService.generate_batch(
            campaign=self.campaign,
            batch_size=10,
            discovery_run=run
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.total_leads_found, 1)
        self.assertIsNotNone(run.completed_at)

    # 14. Prevents concurrent runs
    def test_discover_more_prevents_concurrent_runs(self):
        # Create an active running run
        DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign,
            keyword="Active Search",
            location="Leeds",
            status="running"
        )
        url = reverse("prospecting-campaign-discover-more", kwargs={"pk": self.campaign.id})
        res = self.client.post(url, {"limit": 10})
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data["error"], "DISCOVERY_ALREADY_RUNNING")

    # 15. Does NOT trigger contact enrichment
    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.enrich_lead_contacts_async.delay")
    def test_discover_more_does_not_trigger_contact_enrichment(self, mock_enrich, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Eta Transport", "website": "https://etatransport.example", "phone": "0113555444"}
                ]
            }
        )

        DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        lead = LeadCompany.objects.get(name="Eta Transport")
        self.assertEqual(lead.enrichment_status, "NOT_STARTED")
        mock_enrich.assert_not_called()

    # 16. Does NOT trigger qualification
    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.qualify_lead_async.delay")
    def test_discover_more_does_not_trigger_qualification(self, mock_qualify, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Theta Freight", "website": "https://thetafreight.example", "phone": "0113666555"}
                ]
            }
        )

        DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        lead = LeadCompany.objects.get(name="Theta Freight")
        insight = CampaignLeadInsight.objects.get(company=lead, campaign=self.campaign)
        self.assertEqual(insight.qualification_status, "NOT_STARTED")
        mock_qualify.assert_not_called()

    # 17. Does NOT trigger buying group
    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.identify_buying_group_async.delay")
    def test_discover_more_does_not_trigger_buying_group(self, mock_bg, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Iota Logistics", "website": "https://iotalogistics.example", "phone": "0113888777"}
                ]
            }
        )

        DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        lead = LeadCompany.objects.get(name="Iota Logistics")
        insight = CampaignLeadInsight.objects.get(company=lead, campaign=self.campaign)
        self.assertEqual(insight.buying_group_status, "NOT_STARTED")
        mock_bg.assert_not_called()

    # 18. Does NOT trigger sales guidance
    @patch("llm.tools.executor.ToolExecutor.execute")
    @patch("prospecting.tasks.generate_sales_guidance_async.delay")
    def test_discover_more_does_not_trigger_sales_guidance(self, mock_sg, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult

        mock_exec.return_value = ToolResult(
            success=True,
            tool_name="search_companies",
            data={
                "companies": [
                    {"name": "Kappa Delivery", "website": "https://kappadelivery.example", "phone": "0113222111"}
                ]
            }
        )

        DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        lead = LeadCompany.objects.get(name="Kappa Delivery")
        insight = CampaignLeadInsight.objects.get(company=lead, campaign=self.campaign)
        self.assertEqual(insight.sales_guidance_status, "NOT_STARTED")
        mock_sg.assert_not_called()

    # 19. Provider failure is handled gracefully
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_provider_failure(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult, ToolError

        # First call fails, second call succeeds
        mock_exec.side_effect = [
            ToolResult(success=False, tool_name="search_companies", data={}, error=ToolError(code="API_ERROR", message="API limit exceeded", retryable=False)),
            ToolResult(success=True, tool_name="search_companies", data={"companies": [{"name": "Lambda Cargo", "website": "https://lambda.example"}]}),
            ToolResult(success=True, tool_name="search_companies", data={"results": []})
        ]

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["leads_created"], 1)

    # 20. Partial success across providers
    @patch("llm.tools.executor.ToolExecutor.execute")
    def test_discover_more_partial_success(self, mock_exec):
        from prospecting.discovery.service import DiscoveryBatchService
        from llm.tools.result import ToolResult, ToolError

        mock_exec.side_effect = [
            ToolResult(success=True, tool_name="search_companies", data={"companies": [{"name": "Mu Express", "website": "https://muexpress.example"}]}),
            ToolResult(success=False, tool_name="search_companies", data={}, error=ToolError(code="PROVIDER_DOWN", message="Provider down", retryable=False)),
            ToolResult(success=True, tool_name="search_companies", data={"results": [{"title": "Nu Logistics", "url": "https://nulogistics.example"}]})
        ]

        result = DiscoveryBatchService.generate_batch(campaign=self.campaign, batch_size=10)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["leads_created"], 2)










