import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import requests
from django.test import SimpleTestCase

from .publishing.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTemporaryFailureError,
    ProviderUnknownOutcomeError,
    ProviderValidationError,
)
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
    PublishNowRequest,
    PublishOutcome,
    PublishingMediaType,
    PublishingNetwork,
    SocialAccount,
    ValidatePostRequest,
    WebhookRequest,
)
from .publishing.upload_post import UploadPostConfig, UploadPostProvider


class MockResponse:
    def __init__(self, status_code, payload=None, *, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


class UploadPostProviderTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        self.session = Mock()
        self.config = UploadPostConfig(
            api_base_url="https://api.example.invalid/api",
            api_key="super-secret-api-key",
            webhook_secret="super-secret-webhook-key",
            connection_return_url="https://app.example.com/social/return",
            timeout_seconds=12,
        )
        self.provider = UploadPostProvider(
            session=self.session,
            config=self.config,
            now=lambda: self.now,
        )
        self.workspace_id = uuid4()
        self.profile_id = self.provider.profile_id_for_workspace(self.workspace_id)
        self.account = SocialAccount(
            provider_profile_id=self.profile_id,
            provider_account_id="urn:li:organization:12345",
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

    def test_connection_url_creates_distinct_workspace_profile_and_uses_generic_copy(self):
        self.session.request.side_effect = [
            MockResponse(201, {"success": True, "profile": {"username": self.profile_id}}),
            MockResponse(200, {"success": True, "access_url": "https://connect.example.invalid/token"}),
        ]

        result = self.provider.get_connection_url(ConnectionUrlRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://ignored.example.com/callback",
            state="signed-state",
            requested_networks=(PublishingNetwork.LINKEDIN,),
        ))

        create_call, jwt_call = self.session.request.call_args_list
        self.assertEqual(create_call.args[:2], ("POST", "https://api.example.invalid/api/uploadposts/users"))
        self.assertEqual(create_call.kwargs["json"]["username"], self.profile_id)
        self.assertEqual(jwt_call.kwargs["json"]["platforms"], ["linkedin"])
        self.assertEqual(jwt_call.kwargs["json"]["connect_title"], "Social account connection")
        self.assertEqual(
            jwt_call.kwargs["json"]["redirect_url"],
            "https://app.example.com/social/return?state=signed-state",
        )
        self.assertNotIn("Upload Post", str(jwt_call.kwargs["json"]))
        self.assertEqual(result.url, "https://connect.example.invalid/token")
        self.assertEqual(result.expires_at, self.now + timedelta(hours=48))
        self.assertNotIn("token", repr(result).lower())
        self.assertNotIn(self.config.api_key, repr(self.config))
        self.assertNotIn(self.config.webhook_secret, repr(self.config))

        another_workspace = uuid4()
        self.assertNotEqual(
            self.profile_id,
            self.provider.profile_id_for_workspace(another_workspace),
        )

    def test_connection_refresh_lists_only_linkedin_company_pages_for_workspace_profile(self):
        pages = {
            "success": True,
            "pages": [{
                "id": "urn:li:organization:12345",
                "name": "Example Company",
                "account_id": "internal-account-id",
                "picture": "https://example.com/logo.png",
                "followers": 500,
            }],
        }
        self.session.request.side_effect = [MockResponse(200, pages), MockResponse(200, pages)]

        listed = self.provider.list_social_accounts(ListSocialAccountsRequest(
            workspace_id=self.workspace_id,
            provider_connection_id=self.profile_id,
        ))
        completed = self.provider.complete_connection(CompleteConnectionRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://app.example.com/social/return",
            state="state",
            authorization_code="",
        ))

        self.assertEqual(listed.accounts, (self.account,))
        self.assertTrue(completed.connected)
        self.assertEqual(completed.provider_connection_id, self.profile_id)
        self.assertEqual(dict(completed.safe_metadata), {"account_count": 1})
        for call in self.session.request.call_args_list:
            self.assertEqual(call.kwargs["params"], {"profile": self.profile_id})

    def test_profile_identifier_cannot_be_used_across_workspaces(self):
        with self.assertRaises(ProviderAuthenticationError):
            self.provider.list_social_accounts(ListSocialAccountsRequest(
                workspace_id=uuid4(),
                provider_connection_id=self.profile_id,
            ))
        self.session.request.assert_not_called()

    def test_publish_text_passes_idempotency_and_company_page(self):
        self.session.request.return_value = MockResponse(200, {
            "success": True,
            "request_id": "request-123",
            "total_platforms": 1,
        })

        result = self.provider.publish_now(self.publish_request())

        call = self.session.request.call_args
        self.assertEqual(call.args[:2], ("POST", "https://api.example.invalid/api/upload_text"))
        self.assertEqual(call.kwargs["headers"]["Idempotency-Key"], "job-idempotency-key")
        self.assertIn(("request_id", "job-idempotency-key"), call.kwargs["data"])
        self.assertIn(("target_linkedin_page_id", "urn:li:organization:12345"), call.kwargs["data"])
        self.assertEqual(result.outcome, PublishOutcome.ACCEPTED)
        self.assertEqual(result.external_id, "request-123")

    def test_publish_one_or_multiple_images_uses_mocked_media_downloads(self):
        image_one = MockResponse(200, content=b"image-one", headers={"Content-Type": "image/png"})
        image_two = MockResponse(200, content=b"image-two", headers={"Content-Type": "image/jpeg"})
        self.session.get.side_effect = [image_one, image_two]
        self.session.request.return_value = MockResponse(200, {
            "success": True,
            "request_id": "photo-request-1",
        })
        media = (
            PostMedia(PublishingMediaType.IMAGE, "https://assets.example.com/one.png"),
            PostMedia(PublishingMediaType.MULTI_IMAGE, "https://assets.example.com/two.jpg"),
        )

        result = self.provider.publish_now(self.publish_request(media=media))

        call = self.session.request.call_args
        self.assertEqual(call.args[1], "https://api.example.invalid/api/upload_photos")
        self.assertEqual(len(call.kwargs["files"]), 2)
        self.assertEqual(call.kwargs["files"][0][0], "photos[]")
        self.assertIn(("description", "A company update\n\n#Update"), call.kwargs["data"])
        self.assertEqual(result.outcome, PublishOutcome.ACCEPTED)

    def test_sync_publish_response_is_normalized_as_published(self):
        self.session.request.return_value = MockResponse(200, {
            "success": True,
            "results": {
                "linkedin": {
                    "success": True,
                    "post_id": "linkedin-post-1",
                    "url": "https://www.linkedin.com/feed/update/example",
                },
            },
        })

        result = self.provider.publish_now(self.publish_request())

        self.assertEqual(result.outcome, PublishOutcome.PUBLISHED)
        self.assertEqual(result.external_id, "linkedin-post-1")
        self.assertEqual(
            result.safe_metadata["post_url"],
            "https://www.linkedin.com/feed/update/example",
        )

    def test_validation_rejects_unsupported_network_media_and_long_copy(self):
        request = ValidatePostRequest(
            account=self.account,
            post=NormalizedPost(
                network=PublishingNetwork.X,
                text="x" * 3001,
                media=(PostMedia(PublishingMediaType.VIDEO, "https://assets.example.com/video.mp4"),),
            ),
        )

        result = self.provider.validate_post(request)

        self.assertFalse(result.valid)
        self.assertEqual(
            {issue.code for issue in result.errors},
            {"NETWORK_MISMATCH", "NETWORK_UNSUPPORTED", "MEDIA_UNSUPPORTED", "TEXT_TOO_LONG"},
        )

    def test_status_and_scheduled_cancellation_are_normalized(self):
        self.session.request.side_effect = [
            MockResponse(200, {
                "request_id": "request-123",
                "status": "completed",
                "results": [{"platform": "linkedin", "success": True}],
            }),
            MockResponse(200, {"success": True}),
        ]

        status = self.provider.get_publish_status(GetPublishStatusRequest(
            workspace_id=self.workspace_id,
            external_id="request-123",
        ))
        cancelled = self.provider.cancel_publish(CancelPublishRequest(
            workspace_id=self.workspace_id,
            external_id="scheduled/job id",
        ))

        self.assertEqual(status.outcome, PublishOutcome.PUBLISHED)
        self.assertTrue(cancelled.cancelled)
        self.assertEqual(
            self.session.request.call_args_list[1].args[1],
            "https://api.example.invalid/api/uploadposts/schedule/scheduled%2Fjob%20id",
        )

    def test_post_metrics_are_normalized_and_missing_values_stay_unavailable(self):
        self.session.request.return_value = MockResponse(200, {
            "captured_at": self.now.isoformat(),
            "platforms": {"linkedin": {
                "platform_post_id": "urn:li:share:123",
                "post_metrics_source": "platform_api",
                "post_metrics": {"impressions": 1200, "reactions": 45, "comments": 6},
            }},
        })

        result = self.provider.get_post_metrics(GetPostMetricsRequest(
            workspace_id=self.workspace_id,
            external_id="request-123",
            provider_profile_id=self.profile_id,
            provider_account_id=self.account.provider_account_id,
        ))

        self.assertEqual({item.metric_name.value: item.value for item in result.observations}, {
            "IMPRESSIONS": 1200, "REACTIONS": 45, "COMMENTS": 6,
        })
        self.assertIn("CLICKS", {item.value for item in result.unavailable_metrics})
        self.assertEqual(result.observations[0].raw_reference["platform_post_id"], "urn:li:share:123")

    def test_account_follower_growth_is_explicitly_unavailable_without_a_growth_value(self):
        result = self.provider.get_account_metrics(GetAccountMetricsRequest(
            workspace_id=self.workspace_id,
            network=PublishingNetwork.LINKEDIN,
            provider_profile_id=self.profile_id,
            provider_account_id=self.account.provider_account_id,
        ))

        self.assertEqual(result.observations, ())
        self.assertIn("FOLLOWER_GROWTH", {item.value for item in result.unavailable_metrics})
        self.session.request.assert_not_called()

    def test_signed_webhook_is_verified_normalized_and_replay_protected(self):
        payload = {
            "event": "upload_completed",
            "job_id": "job-123",
            "profile_username": self.profile_id,
            "platform": "linkedin",
            "result": {
                "success": True,
                "publish_id": "published-123",
                "url": "https://www.linkedin.com/feed/update/example",
                "access_token": "must-not-be-copied",
            },
            "created_at": self.now.isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(self.now.timestamp()))
        signature = hmac.new(
            self.config.webhook_secret.encode(),
            f"{timestamp}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        request = WebhookRequest(
            headers={
                "X-Upload-Post-Signature": f"sha256={signature}",
                "X-Upload-Post-Timestamp": timestamp,
                "X-Upload-Post-Delivery": "delivery-1",
                "Authorization": "secret-header",
            },
            body=body,
            received_at=self.now,
        )

        result = self.provider.verify_and_parse_webhook(request)

        event = result.events[0]
        self.assertTrue(result.verified)
        self.assertEqual(event.outcome, PublishOutcome.PUBLISHED)
        self.assertEqual(event.external_id, "published-123")
        self.assertEqual(event.safe_metadata["delivery_id"], "delivery-1")
        self.assertNotIn("must-not-be-copied", repr(result))
        self.assertNotIn("secret-header", repr(request))

        stale = WebhookRequest(
            headers={
                "X-Upload-Post-Signature": f"sha256={signature}",
                "X-Upload-Post-Timestamp": timestamp,
            },
            body=body,
            received_at=self.now + timedelta(minutes=6),
        )
        with self.assertRaises(ProviderAuthenticationError):
            self.provider.verify_and_parse_webhook(stale)

    def test_http_failures_map_to_normalized_errors_without_vendor_branding(self):
        cases = [
            (MockResponse(401, {"message": "provider-specific credential text"}), ProviderAuthenticationError),
            (MockResponse(429, {"message": "quota"}, headers={"Retry-After": "45"}), ProviderRateLimitError),
            (MockResponse(503, {"error": "internal details"}), ProviderTemporaryFailureError),
        ]
        for response, expected_error in cases:
            with self.subTest(status=response.status_code):
                self.session.request.reset_mock()
                self.session.request.side_effect = None
                self.session.request.return_value = response
                with self.assertRaises(expected_error) as caught:
                    self.provider.list_social_accounts(ListSocialAccountsRequest(
                        workspace_id=self.workspace_id,
                        provider_connection_id=self.profile_id,
                    ))
                self.assertNotIn("Upload Post", str(caught.exception))
                self.assertNotIn("provider-specific", str(caught.exception))

        self.session.request.side_effect = requests.Timeout("secret transport details")
        with self.assertRaises(ProviderUnknownOutcomeError):
            self.provider.publish_now(self.publish_request())

    def test_health_check_is_mocked_and_does_not_return_secrets(self):
        self.session.request.return_value = MockResponse(200, {"api_usage": {"count": 1}})

        health = self.provider.health_status(HealthStatusRequest(workspace_id=self.workspace_id))

        self.assertTrue(health.configured)
        self.assertTrue(health.healthy)
        self.assertNotIn(self.config.api_key, repr(health))
        self.assertEqual(self.session.request.call_args.args[1], "https://api.example.invalid/api/uploadposts/me")

        missing_webhook = UploadPostProvider(
            session=Mock(),
            config=UploadPostConfig(
                api_base_url=self.config.api_base_url,
                api_key=self.config.api_key,
                webhook_secret="",
                connection_return_url=self.config.connection_return_url,
            ),
            now=lambda: self.now,
        ).health_status(HealthStatusRequest())
        self.assertFalse(missing_webhook.configured)
