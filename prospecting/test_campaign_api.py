from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from knowledge_base.models import UserProfile
from prospecting.models import (
    DiscoveryRun,
    DiscoveryLead,
    LeadCompany,
    ProspectingCampaign,
    Workspace,
    get_default_workspace,
)


class ProspectingCampaignAPITests(APITestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(
            username="campaign_api_user",
            email="campaign-api@example.com",
        )
        self.workspace = get_default_workspace()
        self.campaign = ProspectingCampaign.objects.create(
            workspace=self.workspace,
            name="London Logistics",
            product_description="Routing software",
            problem_statement="Manual route planning",
            created_by=self.user,
            status="ACTIVE",
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign,
            keyword="logistics",
            location="London",
        )
        self.direct_lead = LeadCompany.objects.create(
            campaign=self.campaign,
            name="Direct Campaign Lead",
            category="Logistics",
        )
        self.run_lead = LeadCompany.objects.create(
            discovery_run=self.run,
            name="Discovery Run Lead",
            category="Courier",
        )
        self.deduplicated_lead = LeadCompany.objects.create(
            name="Deduplicated Campaign Lead",
            category="Logistics",
        )
        DiscoveryLead.objects.create(
            discovery_run=self.run,
            company=self.deduplicated_lead,
        )

        other_workspace = Workspace.objects.create(name="Other Campaign Workspace")
        self.other_campaign = ProspectingCampaign.objects.create(
            workspace=other_workspace,
            name="Hidden Campaign",
            product_description="Other",
            problem_statement="Other",
            created_by=self.user,
        )

    def test_lists_only_current_workspace_campaigns_with_counts(self):
        response = self.client.get(reverse("prospecting-campaigns-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["campaigns"]), 1)
        campaign = response.data["campaigns"][0]
        self.assertEqual(campaign["name"], "London Logistics")
        self.assertEqual(campaign["lead_count"], 3)
        self.assertEqual(campaign["discovery_run_count"], 1)

    def test_retrieves_campaign_and_hides_other_workspace(self):
        response = self.client.get(
            reverse("prospecting-campaign-detail", kwargs={"pk": self.campaign.id})
        )
        hidden = self.client.get(
            reverse("prospecting-campaign-detail", kwargs={"pk": self.other_campaign.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.campaign.id))
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)

    def test_lists_direct_and_discovery_run_campaign_leads(self):
        response = self.client.get(
            reverse("prospecting-campaign-leads", kwargs={"pk": self.campaign.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_count"], 3)
        self.assertEqual(
            {lead["name"] for lead in response.data["leads"]},
            {
                "Direct Campaign Lead",
                "Discovery Run Lead",
                "Deduplicated Campaign Lead",
            },
        )

    def test_existing_leads_endpoint_accepts_campaign_filter(self):
        response = self.client.get(
            reverse("prospecting-leads"),
            {"campaign_id": str(self.campaign.id), "category": "Courier"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_count"], 1)
        self.assertEqual(response.data["leads"][0]["name"], "Discovery Run Lead")
