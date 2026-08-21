import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from llm.tools.implementations.discovery_tools import SearchCompaniesTool
from prospecting.discovery.dto import DiscoveryRequest
from prospecting.discovery.providers.apollo import ApolloProvider
from prospecting.discovery.providers.google_places import GooglePlacesProvider
from prospecting.exceptions import DiscoveryError


class GooglePlacesProviderTestCase(SimpleTestCase):
    @patch("prospecting.discovery.providers.google_places.requests.post")
    def test_search_normalizes_places_response(self, mock_post):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "places": [
                {
                    "id": "place-1",
                    "displayName": {"text": "Acme Dental"},
                    "formattedAddress": "1 High Street, London",
                    "nationalPhoneNumber": "020 1234 5678",
                    "primaryType": "dentist",
                    "rating": 4.7,
                    "websiteUri": "https://acme.example",
                }
            ]
        }
        mock_post.return_value = response

        with patch.dict(
            os.environ,
            {"GOOGLE_PLACES_ENABLED": "true", "GOOGLE_PLACES_API_KEY": "secret"},
            clear=False,
        ):
            result = GooglePlacesProvider().search(
                DiscoveryRequest(query="dentist", location="London", limit=5)
            )

        self.assertEqual(result.provider, "google_places")
        self.assertEqual(result.results[0].name, "Acme Dental")
        self.assertEqual(result.results[0].website, "https://acme.example")
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Goog-Api-Key"], "secret")

    def test_disabled_flag_prevents_search(self):
        with patch.dict(
            os.environ,
            {"GOOGLE_PLACES_ENABLED": "false", "GOOGLE_PLACES_API_KEY": "secret"},
            clear=False,
        ):
            with self.assertRaisesMessage(DiscoveryError, "disabled"):
                GooglePlacesProvider().search(
                    DiscoveryRequest(query="dentist", location="London")
                )


class ApolloProviderTestCase(SimpleTestCase):
    @patch("prospecting.discovery.providers.apollo.requests.post")
    def test_search_normalizes_organizations_response(self, mock_post):
        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {
            "organizations": [
                {
                    "id": "org-1",
                    "name": "Acme Logistics",
                    "primary_domain": "acme.example",
                    "primary_phone": {"number": "+1 555 0100"},
                    "industry": "logistics & supply chain",
                    "city": "Chicago",
                    "state": "Illinois",
                    "country": "United States",
                }
            ],
            "pagination": {"page": 1, "total_pages": 2},
        }
        mock_post.return_value = response

        with patch.dict(
            os.environ,
            {"APOLLO_ENABLED": "yes", "APOLLO_API_KEY": "secret"},
            clear=False,
        ):
            result = ApolloProvider().search(
                DiscoveryRequest(query="logistics", location="Chicago", limit=10)
            )

        self.assertEqual(result.provider, "apollo")
        self.assertEqual(result.results[0].name, "Acme Logistics")
        self.assertEqual(result.results[0].website, "https://acme.example")
        self.assertEqual(result.results[0].category, "logistics & supply chain")
        self.assertEqual(result.next_page_token, "2")
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["x-api-key"], "secret")
        params = mock_post.call_args.kwargs["params"]
        self.assertIn(("q_organization_keyword_tags[]", "logistics"), params)
        self.assertIn(("organization_locations[]", "Chicago"), params)

    def test_disabled_flag_prevents_search(self):
        with patch.dict(
            os.environ,
            {"APOLLO_ENABLED": "off", "APOLLO_API_KEY": "secret"},
            clear=False,
        ):
            with self.assertRaisesMessage(DiscoveryError, "disabled"):
                ApolloProvider().search(
                    DiscoveryRequest(query="logistics", location="Chicago")
                )

    @patch("prospecting.discovery.providers.apollo.requests.post")
    def test_search_companies_tool_dispatches_to_apollo(self, mock_post):
        response = MagicMock(status_code=200, headers={})
        response.json.return_value = {
            "organizations": [
                {
                    "id": "org-2",
                    "name": "Nomad Freight",
                    "website_url": "https://nomad-freight.example",
                    "industry": "transportation",
                }
            ],
            "pagination": {"page": 1, "total_pages": 1},
        }
        mock_post.return_value = response

        with patch.dict(
            os.environ,
            {"APOLLO_ENABLED": "true", "APOLLO_API_KEY": "secret"},
            clear=False,
        ):
            result = SearchCompaniesTool().execute(
                query="freight",
                geography="London",
                limit=5,
                provider="apollo",
            )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "apollo")
        self.assertEqual(result.data["companies"][0]["name"], "Nomad Freight")
        self.assertEqual(result.data["companies"][0]["category"], "transportation")
        self.assertEqual(result.data["companies"][0]["source"], "apollo")
