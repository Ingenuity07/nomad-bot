from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch
from django.utils import timezone

from knowledge_base.models import UserProfile
from prospecting.models import (
    ProspectingRequest, ProspectingSpecificationVersion, Discovery, DiscoveryRun, LeadCompany, DiscoveryLead
)
from prospecting.intent.schemas import ProspectingSpecification, IntentParseResult, Provenance, ObjectiveType
from prospecting.intent.validator import ProspectingSpecificationValidator, SpecificationValidationError
from prospecting.intent.clarifier import ProspectingIntakeClarifier
from prospecting.intent.service import ProspectingIntentService
from prospecting.tasks import discover_campaign_async as original_discover_campaign_async

def get_test_user():
    user, _ = UserProfile.objects.get_or_create(
        username='default_user',
        defaults={'email': 'default@example.com', 'full_name': 'Shivam Singh'}
    )
    return user


class ProspectingSpecificationTestCase(TestCase):
    """Test case verifying schema validation and programmatic business rule checks."""

    def test_valid_specification(self):
        spec = ProspectingSpecification()
        spec.objective.value = "Find courier services in Leeds"
        spec.objective.provenance = Provenance.EXPLICIT_USER
        spec.geography.countries.value = ["United Kingdom"]
        spec.company_constraints.min_employees.value = 10
        spec.company_constraints.max_employees.value = 50

        errors = ProspectingSpecificationValidator.validate_specification(spec)
        self.assertEqual(len(errors), 0)

    def test_invalid_employee_bounds(self):
        spec = ProspectingSpecification()
        spec.company_constraints.min_employees.value = 100
        spec.company_constraints.max_employees.value = 50

        errors = ProspectingSpecificationValidator.validate_specification(spec)
        self.assertIn("min_employees (100) cannot be greater than max_employees (50).", errors)

    def test_invalid_revenue_bounds(self):
        spec = ProspectingSpecification()
        spec.company_constraints.min_revenue.value = 500000
        spec.company_constraints.max_revenue.value = 100000

        errors = ProspectingSpecificationValidator.validate_specification(spec)
        self.assertIn("min_revenue (500000) cannot be greater than max_revenue (100000).", errors)

    def test_contradictory_geography_exclusion(self):
        spec = ProspectingSpecification()
        spec.geography.countries.value = ["United Kingdom"]
        spec.exclusion_rules.value = ["United Kingdom"]

        errors = ProspectingSpecificationValidator.validate_specification(spec)
        self.assertIn("Geography contradiction: 'United Kingdom' cannot be target country and excluded rule simultaneously.", errors)

    def test_confirm_all_inferred(self):
        spec = ProspectingSpecification()
        spec.objective.value = "Test Objective"
        spec.objective.provenance = Provenance.LLM_INFERRED
        spec.target.description.value = "Test Target"
        spec.target.description.provenance = Provenance.LLM_INFERRED

        spec.confirm_all_inferred()
        self.assertEqual(spec.objective.provenance, Provenance.USER_CONFIRMED)
        self.assertEqual(spec.target.description.provenance, Provenance.USER_CONFIRMED)


class ClarificationEngineTestCase(TestCase):
    """Test case verifying missing critical fields detection."""

    def test_missing_critical_objective(self):
        spec = ProspectingSpecification()
        spec.objective.value = ""
        spec.target.description.value = "Some target description"

        missing = ProspectingIntakeClarifier.get_missing_fields(spec)
        self.assertIn("objective", missing)

    def test_missing_critical_target(self):
        spec = ProspectingSpecification()
        spec.objective.value = "Some objective"
        spec.target.description.value = ""

        missing = ProspectingIntakeClarifier.get_missing_fields(spec)
        self.assertIn("target_description", missing)


class IntentParserTestCase(TestCase):
    """Test case verifying parser service and JSON extraction logic."""

    @patch('llm.router.IntelligentRouter.generate')
    def test_successful_parse(self, mock_generate):
        mock_generate.return_value = {
            "type": "text",
            "provider": "gemini-flash",
            "model": "gemini-2.5-flash",
            "text": """
            {
              "status": "READY_FOR_REVIEW",
              "specification": {
                "objective_type": { "value": "SELL", "provenance": "LLM_INFERRED" },
                "objective": { "value": "Sell fleet tracking software", "provenance": "EXPLICIT_USER" },
                "target": {
                  "entity_type": { "value": "COMPANY", "provenance": "SYSTEM_DEFAULT" },
                  "description": { "value": "Logistics companies in Leeds", "provenance": "EXPLICIT_USER" },
                  "industries": { "value": ["Logistics"], "provenance": "LLM_INFERRED" },
                  "categories": { "value": ["Couriers"], "provenance": "LLM_INFERRED" }
                },
                "problem_hypothesis": {
                  "problem": { "value": "Inefficient vehicle routing", "provenance": "LLM_INFERRED" },
                  "solution_or_offering": { "value": "Route planner", "provenance": "LLM_INFERRED" },
                  "relationship": { "value": "Direct match", "provenance": "LLM_INFERRED" }
                },
                "qualification_signals": { "value": ["vehicles"], "provenance": "LLM_INFERRED" },
                "geography": {
                  "countries": { "value": ["UK"], "provenance": "EXPLICIT_USER" },
                  "regions": { "value": [], "provenance": "SYSTEM_DEFAULT" },
                  "cities": { "value": ["Leeds"], "provenance": "EXPLICIT_USER" },
                  "radius": { "value": null, "provenance": "SYSTEM_DEFAULT" },
                  "scope": { "value": "", "provenance": "SYSTEM_DEFAULT" }
                },
                "company_constraints": {
                  "min_employees": { "value": null, "provenance": "SYSTEM_DEFAULT" },
                  "max_employees": { "value": null, "provenance": "SYSTEM_DEFAULT" },
                  "min_revenue": { "value": null, "provenance": "SYSTEM_DEFAULT" },
                  "max_revenue": { "value": null, "provenance": "SYSTEM_DEFAULT" },
                  "company_types": { "value": [], "provenance": "SYSTEM_DEFAULT" }
                },
                "people_constraints": {
                  "roles": { "value": [], "provenance": "SYSTEM_DEFAULT" },
                  "departments": { "value": [], "provenance": "SYSTEM_DEFAULT" },
                  "seniority": { "value": [], "provenance": "SYSTEM_DEFAULT" },
                  "functions": { "value": [], "provenance": "SYSTEM_DEFAULT" }
                },
                "exclusion_rules": { "value": [], "provenance": "SYSTEM_DEFAULT" },
                "requested_information": { "value": [], "provenance": "SYSTEM_DEFAULT" },
                "research_depth": { "value": "standard", "provenance": "SYSTEM_DEFAULT" }
              },
              "missing_information": [],
              "clarification_questions": [],
              "assumptions": ["Fleet tracking is relevant"],
              "confidence": 0.95
            }
            """
        }
        user = get_test_user()
        req = ProspectingIntentService.create_intake_request(
            user_profile=user,
            objective="Sell fleet tracking software",
            target="Logistics companies in Leeds"
        )
        spec_ver = ProspectingIntentService.parse_request(str(req.id))
        self.assertEqual(spec_ver.version, 1)
        self.assertEqual(spec_ver.status, "READY_FOR_REVIEW")
        req.refresh_from_db()
        self.assertEqual(req.status, "READY_FOR_REVIEW")


class ServiceLifecycleTestCase(TestCase):
    """Test case verifying request creation, updates, confirmations, and versioning immutabilities."""

    def setUp(self):
        self.user = get_test_user()

    def test_version_increment_on_specification_update(self):
        req = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective="Selling services",
            status="READY_FOR_REVIEW"
        )
        spec_v1 = ProspectingSpecificationVersion.objects.create(
            request=req,
            version=1,
            specification_json=ProspectingSpecification().model_dump(),
            status="READY_FOR_REVIEW"
        )

        spec = ProspectingSpecification()
        spec.objective.value = "Updated Objective"
        spec.target.description.value = "Updated Target"

        spec_v2 = ProspectingIntentService.update_specification(str(req.id), spec.model_dump())
        self.assertEqual(spec_v2.version, 2)
        self.assertEqual(spec_v2.status, "READY_FOR_REVIEW")
        self.assertEqual(spec_v2.specification_json["objective"]["value"], "Updated Objective")

    def test_cannot_edit_confirmed_specification(self):
        req = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective="Selling services",
            status="CONFIRMED"
        )
        spec_v1 = ProspectingSpecificationVersion.objects.create(
            request=req,
            version=1,
            specification_json=ProspectingSpecification().model_dump(),
            status="CONFIRMED"
        )

        with self.assertRaises(ValueError):
            ProspectingIntentService.update_specification(str(req.id), ProspectingSpecification().model_dump())

    @patch('prospecting.tasks.discover_campaign_async')
    def test_confirm_specification_idempotency(self, mock_discover):
        req = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective="Selling services",
            status="READY_FOR_REVIEW"
        )
        spec = ProspectingSpecification()
        spec.objective.value = "Leeds HVAC Sales"
        spec.target.description.value = "HVAC Contractors"
        spec_ver = ProspectingSpecificationVersion.objects.create(
            request=req,
            version=1,
            specification_json=spec.model_dump(),
            status="READY_FOR_REVIEW"
        )

        # First confirmation
        discovery1 = ProspectingIntentService.confirm_specification(str(req.id), 1, self.user)
        self.assertIsNotNone(discovery1)
        req.refresh_from_db()
        self.assertEqual(req.status, "CONFIRMED")
        self.assertEqual(DiscoveryRun.objects.filter(discovery=discovery1).count(), 1)

        # Second confirmation (should be idempotent and return existing discovery)
        discovery2 = ProspectingIntentService.confirm_specification(str(req.id), 1, self.user)
        self.assertEqual(discovery1.id, discovery2.id)
        self.assertEqual(DiscoveryRun.objects.filter(discovery=discovery1).count(), 1)


class APIEndpointsTestCase(APITestCase):
    """Test case verifying all REST endpoints and status transition permissions."""

    def setUp(self):
        self.user = get_test_user()

    def test_create_intake_and_retrieve_details(self):
        url = reverse('prospecting-intake-list-create')
        data = {
            "objective": "Selling solar panels",
            "target": "Homeowners and roofers",
            "qualification": "Roof size, sunny coordinates"
        }

        # Mock parsing to prevent live LLM call
        with patch('prospecting.intent.parser.ProspectingIntentParser.parse_intent') as mock_parse:
            spec = ProspectingSpecification()
            spec.objective.value = "Selling solar panels"
            spec.target.description.value = "Homeowners"
            mock_parse.return_value = IntentParseResult(
                status="READY_FOR_REVIEW",
                specification=spec,
                missing_information=[],
                clarification_questions=[],
                assumptions=[]
            )

            res = self.client.post(url, data, format='json')
            self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
            req_id = res.data["request"]["id"]

            detail_url = reverse('prospecting-intake-detail', kwargs={'pk': req_id})
            res_detail = self.client.get(detail_url)
            self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
            self.assertEqual(len(res_detail.data["versions"]), 1)

    def test_cancel_intake(self):
        req = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective="Solar panels",
            status="READY_FOR_REVIEW"
        )
        url = reverse('prospecting-intake-cancel', kwargs={'pk': req.id})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        req.refresh_from_db()
        self.assertEqual(req.status, "CANCELLED")


class IntegrationFlowTestCase(TestCase):
    """Test case verifying end-to-end integration mapping from specifications to LeadCompany records."""

    def setUp(self):
        self.user = get_test_user()

    @patch('prospecting.tasks.discover_campaign_async')
    def test_specification_provenance_integration(self, mock_discover):
        req = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective="Selling routes optimizer",
            status="READY_FOR_REVIEW"
        )
        spec = ProspectingSpecification()
        spec.objective.value = "Logistics Sales Leeds"
        spec.target.description.value = "UK Couriers"
        spec.target.categories.value = ["Logistics Courier"]
        spec.geography.countries.value = ["United Kingdom"]

        spec_ver = ProspectingSpecificationVersion.objects.create(
            request=req,
            version=1,
            specification_json=spec.model_dump(),
            status="READY_FOR_REVIEW"
        )

        discovery = ProspectingIntentService.confirm_specification(str(req.id), 1, self.user)
        run = DiscoveryRun.objects.get(discovery=discovery)

        # Verify exact specification version linked on run
        self.assertEqual(run.specification_version.id, spec_ver.id)
        self.assertEqual(run.prospecting_request.id, req.id)

        # Simulate task discovery execution using SearchCompaniesTool mock
        from prospecting.discovery.dto import DiscoveryResultItem
        from prospecting.tasks import discover_campaign_async
        
        with patch('llm.tools.executor.ToolExecutor.execute') as mock_tool_execute:
            mock_tool_execute.return_value = type('ToolResult', (object,), {
                'success': True,
                'data': {
                    'companies': [{
                        'name': 'Leeds Express Logistics',
                        'website': 'https://leedsexpress.co.uk',
                        'phone': None,
                        'address': 'Leeds, UK',
                        'category': 'Courier',
                        'external_id': 'osm-node-1',
                        'raw_metadata': {}
                    }]
                }
            })

            original_discover_campaign_async(str(run.id))
            
            # Verify LeadCompany persisted and linked to DiscoveryRun
            company = LeadCompany.objects.get(name='Leeds Express Logistics')
            self.assertEqual(company.discovery_run.id, run.id)

            # Verify explicit DiscoveryLead junction record created
            dl = DiscoveryLead.objects.get(discovery_run=run, company=company)
            self.assertEqual(dl.company.id, company.id)

            # Verify status endpoint returns completed status and fallback metrics
            status_url = reverse('prospecting-discover-status', kwargs={'pk': run.id})
            res_status = self.client.get(status_url)
            self.assertEqual(res_status.status_code, 200)
            self.assertEqual(res_status.data["status"], "completed")
            self.assertEqual(res_status.data["progress"], 100)
            self.assertEqual(res_status.data["metrics"]["discovered"], 1)
            self.assertEqual(res_status.data["metrics"]["new"], 1)
            self.assertEqual(res_status.data["metrics"]["duplicates"], 0)

            # The Discovery UUID returned by the intake workflow remains a
            # supported status identifier for backwards-compatible clients.
            discovery_status_url = reverse('prospecting-discover-status', kwargs={'pk': discovery.id})
            res_discovery_status = self.client.get(discovery_status_url)
            self.assertEqual(res_discovery_status.status_code, 200)
            self.assertEqual(res_discovery_status.data["run_id"], str(run.id))
