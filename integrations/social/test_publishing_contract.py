import json
from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .publishing.adapters import UploadPostProvider, ZernioProvider
from .publishing.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderPermanentFailureError,
    ProviderRateLimitError,
    ProviderTemporaryFailureError,
    ProviderUnknownOutcomeError,
    ProviderValidationError,
)
from .publishing.fakes import FakeUploadPostProvider, FakeZernioProvider
from .publishing.registry import PublishingProviderRegistry, publishing_provider_registry
from .publishing.types import (
    AccountMetricsResult,
    CancelPublishRequest,
    CompleteConnectionRequest,
    ConnectionUrlRequest,
    GetPublishStatusRequest,
    GetAccountMetricsRequest,
    GetPostMetricsRequest,
    HealthStatusRequest,
    ListSocialAccountsRequest,
    NormalizedPost,
    NormalizedMetricObservation,
    PostMetricsResult,
    PostMedia,
    ProviderCredentials,
    ProviderErrorCategory,
    ProviderName,
    PublishNowRequest,
    PublishOutcome,
    PublishingMediaType,
    PublishingMetricName,
    PublishingNetwork,
    SocialAccount,
    ValidatePostRequest,
    WebhookRequest,
)


class PublishingProviderContractMixin:
    fake_provider_class = None
    provider_name = None

    def setUp(self):
        self.workspace_id = uuid4()
        capabilities = self.fake_provider_class().capabilities
        self.account = SocialAccount(
            provider_profile_id="profile-1",
            provider_account_id="account-1",
            network=PublishingNetwork.LINKEDIN,
            display_name="Example Page",
            account_type="ORGANIZATION",
            capabilities=capabilities,
        )
        self.provider = self.fake_provider_class(accounts=(self.account,))
        self.post = NormalizedPost(
            network=PublishingNetwork.LINKEDIN,
            text="A normalized social post",
            hashtags=("#Social",),
            media=(PostMedia(PublishingMediaType.IMAGE, "https://example.com/image.png"),),
        )

    def publish_request(self, idempotency_key="publish-1"):
        return PublishNowRequest(
            workspace_id=self.workspace_id,
            idempotency_key=idempotency_key,
            account=self.account,
            post=self.post,
        )

    def test_connection_flow_and_account_listing(self):
        connection_url = self.provider.get_connection_url(ConnectionUrlRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://app.example.com/callback",
            state="safe-state",
            requested_networks=(PublishingNetwork.LINKEDIN,),
        ))
        completed = self.provider.complete_connection(CompleteConnectionRequest(
            workspace_id=self.workspace_id,
            redirect_uri="https://app.example.com/callback",
            state="safe-state",
            authorization_code="secret-authorization-code",
        ))
        accounts = self.provider.list_social_accounts(ListSocialAccountsRequest(
            workspace_id=self.workspace_id,
            provider_connection_id=completed.provider_connection_id,
        ))

        self.assertEqual(connection_url.provider, self.provider_name)
        self.assertEqual(connection_url.state, "safe-state")
        self.assertTrue(completed.connected)
        self.assertEqual(accounts.accounts, (self.account,))

    def test_validation_is_normalized(self):
        valid = self.provider.validate_post(ValidatePostRequest(account=self.account, post=self.post))
        invalid = self.provider.validate_post(ValidatePostRequest(
            account=self.account,
            post=NormalizedPost(network=PublishingNetwork.X, text=""),
        ))

        self.assertTrue(valid.valid)
        self.assertFalse(invalid.valid)
        self.assertEqual(
            {issue.code for issue in invalid.errors},
            {"NETWORK_MISMATCH", "EMPTY_POST"},
        )

    def test_publish_is_idempotent_and_status_outcomes_are_distinct(self):
        accepted = self.provider.publish_now(self.publish_request())
        duplicate = self.provider.publish_now(self.publish_request())
        self.assertEqual(accepted.outcome, PublishOutcome.ACCEPTED)
        self.assertEqual(duplicate.external_id, accepted.external_id)

        self.provider.set_publish_outcome(accepted.external_id, PublishOutcome.PUBLISHED)
        published = self.provider.get_publish_status(GetPublishStatusRequest(
            workspace_id=self.workspace_id,
            external_id=accepted.external_id,
        ))
        self.assertEqual(published.outcome, PublishOutcome.PUBLISHED)

        self.provider.set_next_outcome(PublishOutcome.FAILED)
        failed = self.provider.publish_now(self.publish_request("publish-failed"))
        self.assertEqual(failed.outcome, PublishOutcome.FAILED)
        self.assertEqual(failed.error.category, ProviderErrorCategory.PERMANENT_FAILURE)

        unknown = self.provider.get_publish_status(GetPublishStatusRequest(
            workspace_id=self.workspace_id,
            external_id="missing",
        ))
        self.assertEqual(unknown.outcome, PublishOutcome.UNKNOWN)

    def test_cancel_publish_is_normalized(self):
        accepted = self.provider.publish_now(self.publish_request())

        cancelled = self.provider.cancel_publish(CancelPublishRequest(
            workspace_id=self.workspace_id,
            external_id=accepted.external_id,
            idempotency_key="cancel-1",
        ))

        self.assertTrue(cancelled.cancelled)
        self.assertEqual(cancelled.provider_status, "cancelled")

    def test_post_metrics_are_normalized_by_the_contract(self):
        measured_at = datetime.now(timezone.utc)
        self.provider.set_metrics_result(PostMetricsResult(
            provider=self.provider_name,
            observations=(NormalizedMetricObservation(
                PublishingMetricName.IMPRESSIONS,
                125,
                measured_at,
                {"snapshot_id": "safe-reference"},
            ),),
            unavailable_metrics=frozenset({PublishingMetricName.CLICKS}),
            checked_at=measured_at,
        ))
        result = self.provider.get_post_metrics(GetPostMetricsRequest(
            workspace_id=self.workspace_id,
            external_id="published-1",
            provider_profile_id="profile-1",
            provider_account_id="account-1",
        ))
        self.assertEqual(result.observations[0].metric_name, PublishingMetricName.IMPRESSIONS)
        self.assertEqual(result.observations[0].value, 125)
        self.assertIn(PublishingMetricName.CLICKS, result.unavailable_metrics)

    def test_account_metrics_are_normalized_by_the_contract(self):
        measured_at = datetime.now(timezone.utc)
        self.provider.set_account_metrics_result(AccountMetricsResult(
            provider=self.provider_name,
            observations=(NormalizedMetricObservation(
                PublishingMetricName.FOLLOWER_GROWTH,
                12,
                measured_at,
                {"account_id": "safe-reference"},
            ),),
            checked_at=measured_at,
        ))
        result = self.provider.get_account_metrics(GetAccountMetricsRequest(
            workspace_id=self.workspace_id,
            network=PublishingNetwork.LINKEDIN,
            provider_profile_id="profile-1",
            provider_account_id="account-1",
        ))
        self.assertEqual(result.observations[0].metric_name, PublishingMetricName.FOLLOWER_GROWTH)
        self.assertEqual(result.observations[0].value, 12)

    def test_webhook_is_verified_and_reduced_to_safe_normalized_events(self):
        received_at = datetime.now(timezone.utc)
        request = WebhookRequest(
            headers={"X-Fake-Signature": "valid-fake-signature", "Authorization": "secret-token"},
            body=json.dumps({
                "event_type": "publish.completed",
                "outcome": "published",
                "external_id": "external-1",
                "idempotency_key": "publish-1",
                "access_token": "must-not-be-copied",
            }).encode(),
            received_at=received_at,
        )

        parsed = self.provider.verify_and_parse_webhook(request)

        self.assertTrue(parsed.verified)
        self.assertEqual(parsed.events[0].outcome, PublishOutcome.PUBLISHED)
        self.assertEqual(parsed.events[0].external_id, "external-1")
        self.assertEqual(dict(parsed.events[0].safe_metadata), {})
        self.assertNotIn("secret-token", repr(request))
        self.assertNotIn("must-not-be-copied", repr(request))

        with self.assertRaises(ProviderAuthenticationError):
            self.provider.verify_and_parse_webhook(WebhookRequest(headers={}, body=b"{}"))

    def test_health_and_capabilities_are_normalized(self):
        health = self.provider.health_status(HealthStatusRequest(workspace_id=self.workspace_id))

        self.assertTrue(health.configured)
        self.assertTrue(health.healthy)
        self.assertEqual(health.provider, self.provider_name)
        self.assertEqual(
            health.capabilities.networks,
            frozenset({PublishingNetwork.LINKEDIN, PublishingNetwork.X, PublishingNetwork.INSTAGRAM}),
        )
        self.assertEqual(
            health.capabilities.media_types,
            frozenset(PublishingMediaType),
        )


class FakeUploadPostProviderContractTests(PublishingProviderContractMixin, SimpleTestCase):
    fake_provider_class = FakeUploadPostProvider
    provider_name = ProviderName.UPLOAD_POST


class FakeZernioProviderContractTests(PublishingProviderContractMixin, SimpleTestCase):
    fake_provider_class = FakeZernioProvider
    provider_name = ProviderName.ZERNIO


@override_settings(
    UPLOAD_POST_API_KEY="",
    UPLOAD_POST_WEBHOOK_SECRET="",
    UPLOAD_POST_CONNECTION_RETURN_URL="",
)
class PublishingRegistryAndErrorTests(SimpleTestCase):
    def test_default_registry_contains_non_networking_placeholders(self):
        self.assertEqual(
            set(publishing_provider_registry.registered_providers()),
            {ProviderName.UPLOAD_POST, ProviderName.ZERNIO},
        )
        upload_post = publishing_provider_registry.create(ProviderName.UPLOAD_POST)
        zernio = publishing_provider_registry.create(ProviderName.ZERNIO)
        self.assertIsInstance(upload_post, UploadPostProvider)
        self.assertIsInstance(zernio, ZernioProvider)
        self.assertFalse(upload_post.health_status(HealthStatusRequest()).configured)
        with self.assertRaises(ProviderConfigurationError):
            upload_post.publish_now(None)

    def test_registry_can_be_replaced_with_fakes_without_application_models(self):
        registry = PublishingProviderRegistry()
        registry.register(ProviderName.UPLOAD_POST, FakeUploadPostProvider)
        registry.register(ProviderName.ZERNIO, FakeZernioProvider)

        self.assertIsInstance(registry.create("UPLOAD_POST"), FakeUploadPostProvider)
        self.assertIsInstance(registry.create("ZERNIO"), FakeZernioProvider)

    def test_normalized_exception_categories_are_complete(self):
        cases = [
            (ProviderConfigurationError, ProviderErrorCategory.CONFIGURATION),
            (ProviderAuthenticationError, ProviderErrorCategory.AUTHENTICATION),
            (ProviderValidationError, ProviderErrorCategory.VALIDATION),
            (ProviderRateLimitError, ProviderErrorCategory.RATE_LIMIT),
            (ProviderTemporaryFailureError, ProviderErrorCategory.TEMPORARY_FAILURE),
            (ProviderPermanentFailureError, ProviderErrorCategory.PERMANENT_FAILURE),
            (ProviderUnknownOutcomeError, ProviderErrorCategory.UNKNOWN_OUTCOME),
        ]
        for error_class, category in cases:
            with self.subTest(error=error_class.__name__):
                self.assertEqual(error_class("safe message").as_info().category, category)

    def test_secret_bearing_types_have_redacted_representations(self):
        credentials = ProviderCredentials({"access_token": "super-secret-token"})
        completion = CompleteConnectionRequest(
            workspace_id=uuid4(),
            redirect_uri="https://app.example.com/callback",
            state="state",
            authorization_code="secret-code",
        )

        self.assertNotIn("super-secret-token", repr(credentials))
        self.assertNotIn("secret-code", repr(completion))
        error = ProviderTemporaryFailureError(
            "Safe failure",
            safe_details={"access_token": "must-not-leak", "request_id": "safe-request-id"},
        ).as_info()
        self.assertEqual(error.safe_details["access_token"], "<redacted>")
        self.assertEqual(error.safe_details["request_id"], "safe-request-id")
