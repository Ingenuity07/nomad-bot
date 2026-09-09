import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import UUID, uuid4

import requests
from django.test import SimpleTestCase, override_settings

from .publishing.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTemporaryFailureError,
    ProviderUnknownOutcomeError,
    ProviderValidationError,
)
from .publishing.registry import publishing_provider_registry
from .publishing.types import (
    CancelPublishRequest,
    CompleteConnectionRequest,
    ConnectionUrlRequest,
    GetAccountMetricsRequest,
    GetPublishStatusRequest,
    GetPostMetricsRequest,
    HealthStatusRequest,
    ListSocialAccountsRequest,
    NormalizedPost,
    PostMedia,
    ProviderErrorCategory,
    ProviderName,
    PublishNowRequest,
    PublishOutcome,
    PublishingMediaType,
    PublishingNetwork,
    SocialAccount,
    ValidatePostRequest,
    WebhookRequest,
)
from .publishing.zernio import ZernioConfig, ZernioProvider


class MockResponse:
    def __init__(self, status_code, payload=None, *, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class ZernioProviderTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        self.session = Mock()
        self.config = ZernioConfig(
            api_base_url="https://api.example.invalid",
            api_key="super-secret-api-key",
            webhook_secret="super-secret-webhook-key",
            connection_return_url="https://app.example.com/social/return",
            timeout_seconds=12,
        )
        self.provider = ZernioProvider(
            session=self.session,
            config=self.config,
            now=lambda: self.now,
        )
        self.workspace_id = uuid4()
        self.profile_name = self.provider.profile_name_for_workspace(self.workspace_id)
        self.profile_id = "64f0a1b2c3d4e5f6a7b8c9d0"
        self.account_id = "64e1f0a9e2b5af0012ab34cd"
        self.account_payload = {
            "_id": self.account_id,
            "platform": "linkedin",
            "profileId": {"_id": self.profile_id, "name": self.profile_name},
            "username": "example-company",
            "displayName": "Example Company",
            "isActive": True,
            "accountType": "organization",
        }
        self.account = SocialAccount(
            provider_profile_id=self.profile_id,
            provider_account_id=self.account_id,
            network=PublishingNetwork.LINKEDIN,
            display_name="Example Company",
            account_type="ORGANIZATION",
            capabilities=self.provider.capabilities,
        )

    def publish_request(self, *, media=(), idempotency_key="job-idempotency-key"):
        return PublishNowRequest(
            workspace_id=self.workspace_id,
            idempotency_key=idempotency_key,
            account=self.account,
            post=NormalizedPost(
                network=PublishingNetwork.LINKEDIN,
                text="A company update",
                hashtags=("#Update",),
                media=media,
            ),
        )

    def profile_response(self):
        return MockResponse(200, {"profile": {"_id": self.profile_id, "name": self.profile_name}})

    def accounts_response(self):
        return MockResponse(200, {"accounts": [self.account_payload]})

    def test_connection_url_creates_a_separate_profile_and_uses_hosted_flow(self):
        self.session.request.side_effect = [
            MockResponse(201, {"profile": {"_id": self.profile_id, "name": self.profile_name}}),
            MockResponse(200, {"authUrl": "https://connect.example.invalid/oauth"}),
        ]

        result = self.provider.get_connection_url(ConnectionUrlRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://ignored.example.com/callback",
            state="signed-state",
            requested_networks=(PublishingNetwork.LINKEDIN,),
        ))

        create_call, connect_call = self.session.request.call_args_list
        self.assertEqual(create_call.args[:2], ("POST", "https://api.example.invalid/v1/profiles"))
        self.assertEqual(create_call.kwargs["json"]["name"], self.profile_name)
        self.assertEqual(create_call.kwargs["headers"]["Idempotency-Key"], str(self.workspace_id))
        self.assertEqual(connect_call.args[:2], ("GET", "https://api.example.invalid/v1/connect/linkedin"))
        self.assertEqual(connect_call.kwargs["params"]["profileId"], self.profile_id)
        self.assertEqual(connect_call.kwargs["params"]["headless"], "false")
        self.assertEqual(
            connect_call.kwargs["params"]["redirect_url"],
            "https://app.example.com/social/return?state=signed-state",
        )
        self.assertEqual(result.url, "https://connect.example.invalid/oauth")
        self.assertNotIn("oauth", repr(result).lower())
        self.assertNotIn(self.config.api_key, repr(self.config))

    def test_existing_profile_is_reused_after_duplicate_create(self):
        self.session.request.side_effect = [
            MockResponse(409, {"details": {"existingProfileId": self.profile_id}}),
            MockResponse(200, {"authUrl": "https://connect.example.invalid/oauth"}),
        ]

        self.provider.get_connection_url(ConnectionUrlRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://app.example.com/callback",
            state="state",
        ))

        self.assertEqual(
            self.session.request.call_args_list[1].kwargs["params"]["profileId"],
            self.profile_id,
        )

    def test_connection_refresh_lists_only_active_linkedin_company_pages(self):
        personal = {**self.account_payload, "_id": "personal-id", "accountType": "personal"}
        inactive = {**self.account_payload, "_id": "inactive-id", "isActive": False}
        self.session.request.side_effect = [
            self.profile_response(),
            MockResponse(200, {"accounts": [self.account_payload, personal, inactive]}),
        ]

        result = self.provider.list_social_accounts(ListSocialAccountsRequest(
            workspace_id=self.workspace_id,
            provider_connection_id=self.profile_id,
        ))

        self.assertEqual(result.accounts, (self.account,))
        account_call = self.session.request.call_args_list[1]
        self.assertEqual(account_call.kwargs["params"], {
            "profileId": self.profile_id,
            "platform": "linkedin",
            "status": "connected",
        })

    def test_complete_connection_refreshes_accounts_from_workspace_profile(self):
        self.session.request.side_effect = [
            MockResponse(201, {"profile": {"_id": self.profile_id, "name": self.profile_name}}),
            self.accounts_response(),
        ]

        result = self.provider.complete_connection(CompleteConnectionRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://app.example.com/social/return",
            state="state",
            authorization_code="",
        ))

        self.assertTrue(result.connected)
        self.assertEqual(result.provider_connection_id, self.profile_id)
        self.assertEqual(result.safe_metadata["account_count"], 1)

    def test_profile_and_account_cannot_be_used_by_another_workspace(self):
        other_workspace = uuid4()
        self.session.request.return_value = self.profile_response()

        with self.assertRaises(ProviderAuthenticationError):
            self.provider.list_social_accounts(ListSocialAccountsRequest(
                workspace_id=other_workspace,
                provider_connection_id=self.profile_id,
            ))

        self.assertEqual(self.session.request.call_count, 1)

    def test_publish_text_and_images_uses_public_urls_and_stable_idempotency(self):
        self.session.request.side_effect = [
            self.profile_response(),
            self.accounts_response(),
            MockResponse(201, {"post": {
                "_id": "post-123",
                "status": "published",
                "platforms": [{
                    "platform": "linkedin",
                    "platformPostUrl": "https://www.linkedin.com/feed/update/example",
                }],
            }}),
        ]
        media = (
            PostMedia(PublishingMediaType.IMAGE, "https://assets.example.com/one.png"),
            PostMedia(PublishingMediaType.MULTI_IMAGE, "https://assets.example.com/two.jpg"),
        )

        result = self.provider.publish_now(self.publish_request(media=media))

        call = self.session.request.call_args_list[2]
        request_id = call.kwargs["headers"]["x-request-id"]
        UUID(request_id)
        self.assertEqual(request_id, self.provider._stable_request_id("job-idempotency-key"))
        self.assertEqual(call.kwargs["json"]["content"], "A company update\n\n#Update")
        self.assertEqual(call.kwargs["json"]["platforms"], [{
            "platform": "linkedin", "accountId": self.account_id,
        }])
        self.assertEqual(len(call.kwargs["json"]["mediaItems"]), 2)
        self.assertEqual(
            call.kwargs["json"]["metadata"]["nomadIdempotencyKey"],
            "job-idempotency-key",
        )
        self.session.get.assert_not_called()
        self.assertEqual(result.outcome, PublishOutcome.PUBLISHED)
        self.assertEqual(result.external_id, "post-123")

    def test_publish_refuses_an_account_outside_the_workspace_profile(self):
        self.session.request.side_effect = [self.profile_response(), MockResponse(200, {"accounts": []})]

        with self.assertRaises(ProviderAuthenticationError):
            self.provider.publish_now(self.publish_request())

        self.assertEqual(self.session.request.call_count, 2)

    def test_publish_maps_disconnected_and_duplicate_responses_without_vendor_copy(self):
        cases = [
            (
                MockResponse(403, {"code": "ACCOUNT_DISCONNECTED", "error": "vendor detail"}),
                "connection_required",
                ProviderErrorCategory.AUTHENTICATION,
            ),
            (
                MockResponse(409, {
                    "error": "vendor duplicate detail",
                    "details": {"existingPostId": "existing-post"},
                }),
                "duplicate",
                ProviderErrorCategory.UNKNOWN_OUTCOME,
            ),
        ]
        for response, expected_status, expected_category in cases:
            with self.subTest(status=response.status_code):
                self.session.request.reset_mock()
                self.session.request.side_effect = [
                    self.profile_response(), self.accounts_response(), response,
                ]

                result = self.provider.publish_now(self.publish_request())

                expected_outcome = (
                    PublishOutcome.UNKNOWN
                    if response.status_code == 409
                    else PublishOutcome.FAILED
                )
                self.assertEqual(result.outcome, expected_outcome)
                self.assertEqual(result.provider_status, expected_status)
                self.assertEqual(result.error.category, expected_category)
                self.assertNotIn("vendor", result.error.message)
                self.provider._profile_cache.clear()

    def test_validation_enforces_company_page_linkedin_and_current_media_limits(self):
        result = self.provider.validate_post(ValidatePostRequest(
            account=SocialAccount(
                provider_profile_id=self.profile_id,
                provider_account_id=self.account_id,
                network=PublishingNetwork.X,
                display_name="Personal",
                account_type="PERSONAL",
                capabilities=self.provider.capabilities,
            ),
            post=NormalizedPost(
                network=PublishingNetwork.X,
                text="x" * 3001,
                media=tuple(
                    PostMedia(PublishingMediaType.IMAGE, f"https://assets.example.com/{i}.png")
                    for i in range(21)
                ),
            ),
        ))

        self.assertFalse(result.valid)
        self.assertEqual({issue.code for issue in result.errors}, {
            "NETWORK_UNSUPPORTED", "TEXT_TOO_LONG", "TOO_MANY_IMAGES", "ACCOUNT_TYPE_UNSUPPORTED",
        })

    def test_status_cancellation_and_duplicate_content_are_normalized(self):
        self.session.request.side_effect = [
            MockResponse(200, {"post": {"_id": "post-123", "status": "scheduled"}}),
            MockResponse(200, {"message": "Post deleted successfully"}),
        ]

        status = self.provider.get_publish_status(GetPublishStatusRequest(
            workspace_id=self.workspace_id,
            external_id="post-123",
        ))
        cancelled = self.provider.cancel_publish(CancelPublishRequest(
            workspace_id=self.workspace_id,
            external_id="post/123",
        ))

        self.assertEqual(status.outcome, PublishOutcome.ACCEPTED)
        self.assertTrue(cancelled.cancelled)
        self.assertEqual(
            self.session.request.call_args_list[1].args[1],
            "https://api.example.invalid/v1/posts/post%2F123",
        )

    def test_post_metrics_are_normalized_with_a_safe_audit_reference(self):
        self.session.request.return_value = MockResponse(200, {"posts": [{
            "_id": "post-123",
            "platformPostId": "urn:li:share:456",
            "lastUpdated": self.now.isoformat(),
            "analytics": {"impressions": 900, "likes": 31, "comments": 4, "clicks": 8},
        }]})

        result = self.provider.get_post_metrics(GetPostMetricsRequest(
            workspace_id=self.workspace_id,
            external_id="post-123",
            provider_profile_id=self.profile_id,
            provider_account_id=self.account_id,
        ))

        self.assertEqual({item.metric_name.value: item.value for item in result.observations}, {
            "IMPRESSIONS": 900, "LIKES": 31, "COMMENTS": 4, "CLICKS": 8,
        })
        self.assertEqual(result.observations[0].raw_reference["provider_post_id"], "post-123")

    def test_account_follower_growth_uses_the_documented_follower_stats_endpoint(self):
        self.session.request.return_value = MockResponse(200, {
            "accounts": [{
                "_id": self.account_id,
                "platform": "linkedin",
                "currentFollowers": 1250,
                "growth": 50,
            }],
            "dateRange": {"to": self.now.isoformat()},
            "granularity": "daily",
        })

        result = self.provider.get_account_metrics(GetAccountMetricsRequest(
            workspace_id=self.workspace_id,
            network=PublishingNetwork.LINKEDIN,
            provider_profile_id=self.profile_id,
            provider_account_id=self.account_id,
        ))

        self.assertEqual({item.metric_name.value: item.value for item in result.observations}, {
            "FOLLOWER_GROWTH": 50,
        })
        self.assertEqual(
            self.session.request.call_args.args[1],
            "https://api.example.invalid/v1/accounts/follower-stats",
        )
        self.assertEqual(self.session.request.call_args.kwargs["params"]["accountIds"], self.account_id)
        self.assertNotIn("currentFollowers", repr(result))

    def test_signed_webhook_is_verified_and_reduced_to_safe_fields(self):
        payload = {
            "id": "event-1",
            "event": "post.published",
            "timestamp": self.now.isoformat(),
            "post": {
                "_id": "post-123",
                "metadata": {
                    "nomadIdempotencyKey": "job-123",
                    "accessToken": "must-not-be-copied",
                },
                "platforms": [{
                    "platform": "linkedin",
                    "accountId": self.account_id,
                    "platformPostUrl": "https://www.linkedin.com/feed/update/example",
                }],
            },
            "credential": "must-not-be-copied",
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(
            self.config.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        result = self.provider.verify_and_parse_webhook(WebhookRequest(
            headers={
                "X-Zernio-Signature": signature,
                "X-Zernio-Event-Id": "event-1",
                "Authorization": "secret-header",
            },
            body=body,
            received_at=self.now,
        ))

        event = result.events[0]
        self.assertTrue(result.verified)
        self.assertEqual(event.outcome, PublishOutcome.PUBLISHED)
        self.assertEqual(event.external_id, "post-123")
        self.assertEqual(event.idempotency_key, "job-123")
        self.assertEqual(event.safe_metadata["event_id"], "event-1")
        self.assertNotIn("must-not-be-copied", repr(result))
        with self.assertRaises(ProviderAuthenticationError):
            self.provider.verify_and_parse_webhook(WebhookRequest(headers={}, body=body))

        body_without_id = json.dumps({"event": "post.published"}).encode()
        signature_without_id = hmac.new(
            self.config.webhook_secret.encode(), body_without_id, hashlib.sha256
        ).hexdigest()
        with self.assertRaises(ProviderValidationError):
            self.provider.verify_and_parse_webhook(WebhookRequest(
                headers={"X-Zernio-Signature": signature_without_id},
                body=body_without_id,
            ))

    def test_http_failures_use_shared_normalized_categories_and_hide_vendor_details(self):
        cases = [
            (MockResponse(401, {"error": "vendor credential details"}), ProviderAuthenticationError),
            (MockResponse(429, {"error": "quota"}, headers={"Retry-After": "45"}), ProviderRateLimitError),
            (MockResponse(503, {"error": "internal details"}), ProviderTemporaryFailureError),
        ]
        for response, expected_error in cases:
            with self.subTest(status=response.status_code):
                self.session.request.reset_mock()
                self.session.request.side_effect = None
                self.session.request.return_value = response
                with self.assertRaises(expected_error) as caught:
                    self.provider.list_social_accounts(ListSocialAccountsRequest(
                        self.workspace_id, self.profile_id
                    ))
                self.assertNotIn("Zernio", str(caught.exception))
                self.assertNotIn("vendor", str(caught.exception))

        self.provider._profile_cache[self.workspace_id] = self.profile_id
        self.session.request.side_effect = [self.accounts_response(), requests.Timeout("secret details")]
        with self.assertRaises(ProviderUnknownOutcomeError):
            self.provider.publish_now(self.publish_request())

    def test_health_is_mocked_and_registry_builds_the_live_adapter(self):
        self.session.request.return_value = MockResponse(200, {"profiles": []})

        health = self.provider.health_status(HealthStatusRequest(workspace_id=self.workspace_id))

        self.assertTrue(health.configured)
        self.assertTrue(health.healthy)
        self.assertEqual(health.provider, ProviderName.ZERNIO)
        self.assertNotIn(self.config.api_key, repr(health))

    @override_settings(
        ZERNIO_API_BASE_URL="https://api.example.invalid",
        ZERNIO_API_KEY="configured-key",
        ZERNIO_WEBHOOK_SECRET="configured-secret",
        ZERNIO_CONNECTION_RETURN_URL="https://app.example.com/social/return",
        ZERNIO_HTTP_TIMEOUT_SECONDS=9,
    )
    def test_registry_uses_environment_backed_zernio_config(self):
        adapter = publishing_provider_registry.create(ProviderName.ZERNIO, session=Mock())

        self.assertIsInstance(adapter, ZernioProvider)
        self.assertEqual(adapter._config.api_base_url, "https://api.example.invalid")
        self.assertEqual(adapter._config.timeout_seconds, 9)
        self.assertNotIn("configured-key", repr(adapter._config))
