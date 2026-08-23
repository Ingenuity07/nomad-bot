import json
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from knowledge_base.models import UserProfile
from prospecting.analyzer import WebsiteAnalyzer
from prospecting.models import (
    CampaignLeadInsight,
    DiscoveryLead,
    DiscoveryRun,
    Evidence,
    LeadCompany,
    LeadContact,
    ProspectingCampaign,
    get_default_workspace,
)


class CampaignLeadAnalysisTests(APITestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(
            username='campaign_insight_user',
            email='campaign-insight@example.com',
        )
        self.campaign = ProspectingCampaign.objects.create(
            workspace=get_default_workspace(),
            name='Field Service Campaign',
            product_description='Dispatch and route planning software',
            problem_statement='Manual technician scheduling and routing',
            created_by=self.user,
            status='ACTIVE',
        )
        self.run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=self.campaign,
            keyword='HVAC contractors',
            location='Leeds',
        )
        self.company = LeadCompany.objects.create(
            discovery_run=self.run,
            campaign=self.campaign,
            name='Leeds Heating Services',
            website='https://heating.example.com',
            phone='+44 113 555 0100',
            address='10 Example Road, Leeds',
            category='HVAC',
        )

    @staticmethod
    def _model_payload(score=88):
        return {
            'company_summary': 'A commercial heating maintenance provider.',
            'industry': 'Commercial HVAC',
            'business_model': 'Field service contractor',
            'services': ['Emergency heating repair', 'Planned maintenance'],
            'operational_profile': {
                'has_delivery': False,
                'has_field_service': True,
                'has_scheduling': True,
                'needs_routing': True,
                'fleet_size_estimate': 'unknown',
            },
            'fit_score': score,
            'fit_level': 'HIGH',
            'fit_reason': 'Technicians travel to customer sites and require scheduling.',
            'confidence': 0.9,
            'positive_factors': ['Mobile technicians are dispatched to customer sites.'],
            'negative_factors': [],
            'data_gaps': ['Fleet size is not published.'],
            'recommended_next_step': 'Ask the operations manager how daily jobs are assigned.',
            'talking_points': ['Reference emergency call-out scheduling.'],
            'evidence': [{
                'claim': 'The company dispatches field technicians.',
                'quote': 'We dispatch technicians to customer sites every day.',
                'confidence': 0.95,
            }],
        }

    @patch('prospecting.analyzer.requests.get')
    def test_analyzer_persists_grounded_campaign_specific_result(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = (
            '<html><body>We dispatch technicians to customer sites every day.</body></html>'
        )
        provider = MagicMock()
        provider.generate.return_value = {'type': 'text', 'text': json.dumps(self._model_payload())}

        analyzer = WebsiteAnalyzer(provider=provider)
        analyzer.analyze_website(self.company, campaign=self.campaign)

        insight = CampaignLeadInsight.objects.get(
            company=self.company,
            campaign=self.campaign,
        )
        self.assertEqual(float(insight.fit_score), 88.0)
        self.assertEqual(insight.industry, 'Commercial HVAC')
        self.assertEqual(insight.data_gaps, ['Fleet size is not published.'])
        self.assertEqual(Evidence.objects.filter(company=self.company, campaign=self.campaign).count(), 1)
        self.assertEqual(float(self.company.analysis.lead_score), 8.8)

        call = provider.generate.call_args.kwargs
        self.assertEqual(call['template_variables']['problem_statement'], self.campaign.problem_statement)
        self.assertIn('untrusted evidence', call['system_prompt'])

        provider.generate.return_value = {'type': 'text', 'text': json.dumps(self._model_payload(91))}
        analyzer.analyze_website(self.company, campaign=self.campaign)
        insight.refresh_from_db()
        self.assertEqual(float(insight.fit_score), 91.0)
        self.assertEqual(CampaignLeadInsight.objects.count(), 1)
        self.assertEqual(Evidence.objects.filter(company=self.company, campaign=self.campaign).count(), 1)

    def test_intelligence_api_returns_stored_analysis_and_legacy_contacts(self):
        CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign,
            company_summary='A field service heating company.',
            industry='Commercial HVAC',
            business_model='Field service contractor',
            services=['Emergency repair'],
            fit_score=87,
            fit_level='HIGH',
            fit_reason='Its mobile workforce matches the dispatch campaign.',
            confidence=0.88,
            positive_factors=['Mobile workforce'],
            data_gaps=['Fleet size'],
            recommended_next_step='Confirm the number of field technicians.',
        )
        LeadContact.objects.create(
            company=self.company,
            name='Alex Morgan',
            email='alex@heating.example.com',
            phone='+44 113 555 0101',
            role='Operations Manager',
            source=self.company.website,
        )

        response = self.client.get(
            reverse('lead-intelligence', kwargs={'pk': self.company.id}),
            {'campaign_id': str(self.campaign.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['campaign']['id'], str(self.campaign.id))
        self.assertEqual(response.data['analysis']['fit_score'], 87.0)
        self.assertEqual(response.data['analysis']['summary'], 'A field service heating company.')
        self.assertEqual(response.data['company']['emails'], ['alex@heating.example.com'])
        self.assertEqual(response.data['contacts'][0]['name'], 'Alex Morgan')
        self.assertEqual(response.data['contacts'][0]['contact_points'][0]['value'], 'alex@heating.example.com')

        included = self.client.get(
            reverse('prospecting-campaign-leads', kwargs={'pk': self.campaign.id}),
            {'score_min': '80'},
        )
        excluded = self.client.get(
            reverse('prospecting-campaign-leads', kwargs={'pk': self.campaign.id}),
            {'score_min': '90'},
        )
        self.assertEqual(included.data['total_count'], 1)
        self.assertEqual(included.data['leads'][0]['analysis']['lead_score'], 87.0)
        self.assertEqual(excluded.data['total_count'], 0)

    def test_campaign_id_selects_the_correct_analysis_for_shared_company(self):
        second_campaign = ProspectingCampaign.objects.create(
            workspace=get_default_workspace(),
            name='Accounting Campaign',
            product_description='Accounting software',
            problem_statement='Manual invoicing',
            created_by=self.user,
        )
        second_run = DiscoveryRun.objects.create(
            user_profile=self.user,
            campaign=second_campaign,
            keyword='service companies',
            location='Leeds',
        )
        DiscoveryLead.objects.create(discovery_run=second_run, company=self.company)
        CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=self.campaign,
            fit_score=90,
            fit_level='HIGH',
            fit_reason='Strong dispatch fit.',
        )
        CampaignLeadInsight.objects.create(
            company=self.company,
            campaign=second_campaign,
            fit_score=35,
            fit_level='LOW',
            fit_reason='Only a weak invoicing match.',
        )

        response = self.client.get(
            reverse('lead-intelligence', kwargs={'pk': self.company.id}),
            {'campaign_id': str(second_campaign.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['scores']['overall'], 35.0)
        self.assertEqual(response.data['problem_hypothesis'], 'Only a weak invoicing match.')
