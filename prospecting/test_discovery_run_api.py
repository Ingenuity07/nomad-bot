from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from knowledge_base.models import UserProfile
from prospecting.models import (
    DiscoveryLead,
    DiscoveryRun,
    LeadCompany,
    ProspectingCampaign,
    ProspectingRequest,
    ProspectingSpecificationVersion,
    ResearchRun,
    Workspace,
    get_default_workspace,
)


class DiscoveryRunAPITests(APITestCase):
    databases = {'default'}
    def setUp(self):
        self.user = UserProfile.objects.create(
            username='discovery_run_api_user',
            email='discovery-runs@example.com',
        )
        self.request_record = ProspectingRequest.objects.create(
            user_profile=self.user,
            raw_objective='Sell routing software',
            raw_target='Courier operators',
            raw_qualification='Owns delivery vehicles',
            status='COMPLETED',
        )
        self.specification = ProspectingSpecificationVersion.objects.create(
            request=self.request_record,
            version=1,
            status='CONFIRMED',
        )
        self.campaign = ProspectingCampaign.objects.create(
            workspace=get_default_workspace(),
            name='Courier operators in Leeds',
            product_description='Routing software',
            problem_statement='Manual route planning',
            created_by=self.user,
            prospecting_request=self.request_record,
            status='ACTIVE',
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign,
            keyword='Courier operators',
            location='Leeds',
            status='completed',
            total_leads_found=3,
            prospecting_request=self.request_record,
            specification_version=self.specification,
        )
        self.new_lead = LeadCompany.objects.create(
            discovery_run=self.run,
            campaign=self.campaign,
            name='Leeds Courier Ltd',
            category='Courier',
        )
        DiscoveryLead.objects.create(
            discovery_run=self.run,
            company=self.new_lead,
        )
        self.duplicate_lead = LeadCompany.objects.create(
            name='Existing Logistics Ltd',
            category='Logistics',
        )
        DiscoveryLead.objects.create(
            discovery_run=self.run,
            company=self.duplicate_lead,
        )

        other_workspace = Workspace.objects.create(name='Private Workspace')
        other_campaign = ProspectingCampaign.objects.create(
            workspace=other_workspace,
            name='Private Campaign',
            product_description='Private',
            problem_statement='Private',
            created_by=self.user,
        )
        self.hidden_run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=other_campaign,
            keyword='Private search',
            location='Secret',
        )

    def test_lists_runs_with_campaign_request_and_lead_metrics(self):
        response = self.client.get(reverse('discovery-runs-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_count'], 1)
        run = response.data['discovery_runs'][0]
        self.assertEqual(run['id'], str(self.run.id))
        self.assertEqual(run['campaign']['id'], str(self.campaign.id))
        self.assertEqual(
            run['prospecting_request']['id'],
            str(self.request_record.id),
        )
        self.assertEqual(run['specification_version']['version'], 1)
        self.assertEqual(run['lead_count'], 2)
        self.assertEqual(run['new_lead_count'], 1)
        self.assertEqual(run['duplicate_lead_count'], 1)

    def test_filters_runs_and_supports_pagination(self):
        response = self.client.get(
            reverse('discovery-runs-list'),
            {
                'status': 'completed',
                'search': 'Leeds',
                'campaign_id': str(self.campaign.id),
                'page': 1,
                'page_size': 1,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_count'], 1)
        self.assertEqual(response.data['page_size'], 1)

    def test_retrieves_run_detail_and_hides_other_workspace(self):
        response = self.client.get(
            reverse('discovery-run-detail', kwargs={'pk': self.run.id})
        )
        hidden = self.client.get(
            reverse('discovery-run-detail', kwargs={'pk': self.hidden_run.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['keyword'], 'Courier operators')
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)

    def test_lists_only_leads_connected_to_selected_run(self):
        unrelated = LeadCompany.objects.create(
            campaign=self.campaign,
            name='Unrelated Campaign Lead',
        )
        response = self.client.get(
            reverse('discovery-run-leads', kwargs={'pk': self.run.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_count'], 2)
        self.assertEqual(
            {lead['name'] for lead in response.data['leads']},
            {'Leeds Courier Ltd', 'Existing Logistics Ltd'},
        )
        self.assertNotIn(
            unrelated.name,
            {lead['name'] for lead in response.data['leads']},
        )

    def test_unresearched_leads_are_returned_with_intelligence_locked(self):
        response = self.client.get(
            reverse('discovery-run-leads', kwargs={'pk': self.run.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lead = next(item for item in response.data['leads'] if item['id'] == str(self.new_lead.id))
        self.assertTrue(lead['data_locked'])
        self.assertEqual(lead['research_status'], 'NOT_STARTED')
        self.assertEqual(lead['contacts'], [])
        self.assertEqual(lead['analysis'], {})
        self.assertIsNone(lead['phone'])

        detail = self.client.get(
            reverse('lead-intelligence', kwargs={'pk': self.new_lead.id}),
            {'campaign_id': str(self.campaign.id)},
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertTrue(detail.data['data_locked'])
        self.assertEqual(detail.data['research_status'], 'NOT_STARTED')
        self.assertEqual(detail.data['evidence_timeline'], [])
        self.assertEqual(detail.data['contacts'], [])

    @patch('prospecting.tasks.research_lead_async.delay')
    def test_queues_single_or_bulk_research_only_after_user_submission(self, research_delay):
        self.new_lead.website = 'https://leeds-courier.example'
        self.new_lead.save(update_fields=['website'])
        self.duplicate_lead.website = 'https://existing-logistics.example'
        self.duplicate_lead.save(update_fields=['website'])

        response = self.client.post(
            reverse('discovery-run-research', kwargs={'pk': self.run.id}),
            {'lead_ids': [str(self.new_lead.id), str(self.duplicate_lead.id)]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(len(response.data['queued']), 2)
        self.assertEqual(ResearchRun.objects.filter(campaign=self.campaign, status='QUEUED').count(), 2)
        self.assertEqual(research_delay.call_count, 2)

    @patch('prospecting.tasks.research_lead_async.delay')
    def test_rejects_research_for_a_lead_outside_the_selected_run(self, research_delay):
        unrelated = LeadCompany.objects.create(
            campaign=self.campaign,
            name='Unrelated Research Lead',
            website='https://unrelated.example',
        )

        response = self.client.post(
            reverse('discovery-run-research', kwargs={'pk': self.run.id}),
            {'lead_ids': [str(unrelated.id)]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(research_delay.call_count, 0)

