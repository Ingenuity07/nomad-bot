import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.social.models import (
    ConnectionState,
    MediaAsset,
    MediaAssetType,
    ProviderEvent,
    PublishAttempt,
    PublishJob,
    PublishJobState,
    SocialAccountType,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialProvider,
    SocialWorkspaceSettings,
)
from integrations.social.publishing.errors import (
    ProviderAuthenticationError,
    ProviderTemporaryFailureError,
    ProviderUnknownOutcomeError,
    ProviderValidationError,
)
from integrations.social.publishing.fakes import FakeUploadPostProvider, FakeZernioProvider
from integrations.social.publishing.types import (
    ProviderErrorCategory,
    ProviderErrorInfo,
    ProviderName,
    PublishOutcome,
    PublishResult,
    WebhookRequest,
)
from integrations.social.services.lifecycle import (
    ProviderWebhookReplayError,
    approve_variant,
    cancel_variant,
    claim_due_jobs,
    edit_variant,
    execute_claimed_job,
    process_provider_webhook,
    reconcile_pending_jobs,
    transition_variant,
)
from integrations.social.services.publishing_routing import (
    SocialPublisherAdminService,
    create_publish_job,
)
from prospecting.models import Workspace


@override_settings(
    SOCIAL_PUBLISHER_DEFAULT="UPLOAD_POST",
    SOCIAL_PUBLISH_MAX_ATTEMPTS=3,
    SOCIAL_PUBLISH_RETRY_BASE_SECONDS=10,
    SOCIAL_PUBLISH_RETRY_MAX_SECONDS=60,
    SOCIAL_PUBLISH_CLAIM_TTL_SECONDS=30,
)
class PublishingLifecycleTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Lifecycle workspace")
        self.social_settings = SocialWorkspaceSettings.objects.create(workspace=self.workspace)
        self.connection = SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.UPLOAD_POST,
            provider_profile_id="profile-1",
            provider_account_id="account-1",
            display_name="Example Company",
            account_type=SocialAccountType.ORGANIZATION,
            status=ConnectionState.CONNECTED,
        )
        self.post = SocialPost.objects.create(
            workspace=self.workspace,
            idea_title="Campaign idea",
            state=SocialPostState.NEEDS_REVIEW,
        )
        self.variant = SocialPostVariant.objects.create(
            post=self.post,
            connection=self.connection,
            network=SocialNetwork.LINKEDIN,
            copy="Approved copy",
            hashtags=["#Approved"],
            scheduled_for=timezone.now() - timedelta(minutes=1),
            status=SocialPostState.NEEDS_REVIEW,
        )
        self.fake = FakeUploadPostProvider()
        self.provider_patch = patch(
            "integrations.social.services.lifecycle.publishing_provider_registry.create",
            return_value=self.fake,
        )
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    def schedule(self, *, idempotency_key="logical-job-1"):
        version = approve_variant(self.variant)
        result = create_publish_job(
            variant=self.variant,
            approved_version=version,
            idempotency_key=idempotency_key,
            provider_account_id=self.connection.provider_account_id,
        )
        return PublishJob.objects.get(pk=result.publish_job_id)

    def test_approval_references_an_immutable_snapshot(self):
        MediaAsset.objects.create(
            workspace=self.workspace,
            variant=self.variant,
            asset_type=MediaAssetType.IMAGE,
            storage_url="/media/approved.png",
            content_type="image/png",
            byte_size=1024,
            width=1200,
            height=1200,
        )

        version = approve_variant(self.variant)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.approved_version, version)
        self.assertEqual(version.copy, "Approved copy")
        self.assertEqual(version.hashtags, ["#Approved"])
        self.assertEqual(version.scheduled_for, self.variant.scheduled_for)
        self.assertEqual(version.media_snapshot[0]["storage_url"], "/media/approved.png")
        version.copy = "mutated"
        with self.assertRaises(ValidationError):
            version.save()

    def test_terminal_state_rejects_invalid_transition(self):
        self.variant.status = SocialPostState.PUBLISHED
        self.variant.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            transition_variant(self.variant, SocialPostState.DRAFT)

    def test_editing_approved_content_creates_version_revokes_approval_and_cancels_job(self):
        job = self.schedule()
        version_count = self.variant.versions.count()

        edit_variant(
            self.variant,
            copy="Edited copy",
            hashtags=["#Edited"],
            scheduled_for=self.variant.scheduled_for + timedelta(hours=1),
            media_changed=True,
        )

        self.variant.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.variant.versions.count(), version_count + 1)
        self.assertIsNone(self.variant.approved_version)
        self.assertEqual(self.variant.status, SocialPostState.NEEDS_REVIEW)
        self.assertEqual(job.status, PublishJobState.CANCELLED)

    def test_due_job_claim_is_atomic_and_duplicate_execution_is_ignored(self):
        job = self.schedule()

        first = claim_due_jobs()
        second = claim_due_jobs()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        execute_claimed_job(first[0])
        execute_claimed_job(first[0])
        job.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.SUBMITTED)
        self.assertEqual(job.attempts.count(), 1)
        self.assertEqual(self.fake.calls.count("publish_now"), 1)

    def test_same_approval_cannot_create_duplicate_publish_jobs(self):
        job = self.schedule()

        duplicate = create_publish_job(
            variant=self.variant,
            approved_version=job.approved_version,
            idempotency_key="different-logical-key",
            provider_account_id=self.connection.provider_account_id,
        )

        self.assertEqual(duplicate.publish_job_id, job.id)
        self.assertEqual(PublishJob.objects.filter(variant=self.variant).count(), 1)

    def test_scheduled_job_can_be_cancelled_before_claim(self):
        job = self.schedule()

        cancel_variant(self.variant)

        job.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.CANCELLED)
        self.assertEqual(self.variant.status, SocialPostState.CANCELLED)
        self.assertEqual(claim_due_jobs(), ())

    def test_each_attempt_has_a_database_unique_idempotency_key(self):
        job = self.schedule()
        self.fake.publish_now = Mock(side_effect=ProviderTemporaryFailureError(
            "safe temporary failure",
            safe_details={"request_id": "internal-diagnostic"},
        ))

        first_claim = claim_due_jobs()[0]
        execute_claimed_job(first_claim)
        job.refresh_from_db()
        first_key = job.attempts.get(attempt_number=1).idempotency_key
        second_claim = claim_due_jobs(now=job.next_attempt_at)[0]
        execute_claimed_job(second_claim)
        job.refresh_from_db()
        second_key = job.attempts.get(attempt_number=2).idempotency_key

        self.assertNotEqual(first_key, second_key)
        with self.assertRaises(IntegrityError):
            PublishAttempt.objects.create(
                job=job,
                attempt_number=3,
                idempotency_key=first_key,
            )

    def test_retry_is_bounded_and_diagnostics_are_separate_from_human_message(self):
        job = self.schedule()
        self.fake.publish_now = Mock(side_effect=ProviderTemporaryFailureError(
            "provider-safe-message",
            safe_details={"request_id": "internal-diagnostic"},
        ))

        for _ in range(3):
            job.refresh_from_db()
            now = job.next_attempt_at or timezone.now()
            claim = claim_due_jobs(now=now)[0]
            execute_claimed_job(claim)

        job.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.FAILED)
        self.assertEqual(job.attempt_count, 3)
        self.assertEqual(job.failure_message, "The publishing service is temporarily unavailable.")
        self.assertEqual(job.diagnostic_details["request_id"], "internal-diagnostic")
        self.assertNotIn("internal-diagnostic", job.failure_message)
        self.assertEqual(claim_due_jobs(now=timezone.now() + timedelta(days=1)), ())

    def test_permanent_validation_failure_is_not_retried(self):
        job = self.schedule()
        self.fake.publish_now = Mock(side_effect=ProviderValidationError(
            "provider validation detail",
            safe_details={"field": "media"},
        ))

        execute_claimed_job(claim_due_jobs()[0])

        job.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.FAILED)
        self.assertIsNone(job.next_attempt_at)
        self.assertEqual(job.attempt_count, 1)

    def test_approved_media_is_validated_again_before_provider_call(self):
        job = self.schedule()
        type(job.approved_version).objects.filter(pk=job.approved_version_id).update(
            media_snapshot=[{
                "asset_type": "IMAGE",
                "storage_url": "http://169.254.169.254/latest/meta-data",
                "content_type": "image/png",
                "byte_size": 100,
                "width": 10,
                "height": 10,
            }],
        )

        execute_claimed_job(claim_due_jobs()[0])

        job.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.FAILED)
        self.assertNotIn("publish_now", self.fake.calls)

    def test_unknown_timeout_is_reconciled_before_retry(self):
        job = self.schedule()
        self.fake.publish_now = Mock(side_effect=ProviderUnknownOutcomeError(
            "unknown after timeout"
        ))

        execute_claimed_job(claim_due_jobs()[0])
        job.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.UNKNOWN)
        self.assertEqual(claim_due_jobs(), ())

        self.fake.get_publish_status = Mock(return_value=PublishResult(
            provider=ProviderName.UPLOAD_POST,
            outcome=PublishOutcome.PUBLISHED,
            external_id="external-1",
            provider_status="published",
        ))
        result = reconcile_pending_jobs()

        job.refresh_from_db()
        self.assertEqual(result["published"], 1)
        self.assertEqual(job.status, PublishJobState.PUBLISHED)
        self.assertEqual(self.fake.get_publish_status.call_count, 1)
        self.assertEqual(job.attempt_count, 1)

    def test_submitted_job_is_periodically_reconciled(self):
        job = self.schedule()
        execute_claimed_job(claim_due_jobs()[0])
        job.refresh_from_db()
        self.fake.set_publish_outcome(job.external_id, PublishOutcome.PUBLISHED)

        result = reconcile_pending_jobs()

        job.refresh_from_db()
        self.assertEqual(result["published"], 1)
        self.assertEqual(job.status, PublishJobState.PUBLISHED)

    def test_stale_claim_is_reconciled_without_leaving_variant_publishing(self):
        job = self.schedule()
        claim_due_jobs()
        stale_time = timezone.now() - timedelta(minutes=10)
        PublishJob.objects.filter(pk=job.pk).update(claimed_at=stale_time)
        self.fake.get_publish_status = Mock(return_value=PublishResult(
            provider=ProviderName.UPLOAD_POST,
            outcome=PublishOutcome.UNKNOWN,
            provider_status="not_found",
        ))

        reconcile_pending_jobs()

        job.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.SCHEDULED)
        self.assertEqual(self.variant.status, SocialPostState.SCHEDULED)
        self.assertNotIn("publish_now", self.fake.calls)

    def test_unknown_result_at_retry_limit_becomes_failed(self):
        job = self.schedule()
        PublishJob.objects.filter(pk=job.pk).update(attempt_count=2)
        self.fake.publish_now = Mock(side_effect=ProviderUnknownOutcomeError("timeout"))
        execute_claimed_job(claim_due_jobs()[0])
        self.fake.get_publish_status = Mock(return_value=PublishResult(
            provider=ProviderName.UPLOAD_POST,
            outcome=PublishOutcome.UNKNOWN,
            provider_status="not_found",
        ))

        reconcile_pending_jobs()

        job.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.FAILED)
        self.assertEqual(self.variant.status, SocialPostState.FAILED)
        self.assertIsNone(job.next_attempt_at)

    def test_authentication_failure_moves_to_connection_required(self):
        job = self.schedule()
        self.fake.publish_now = Mock(side_effect=ProviderAuthenticationError("expired token"))

        execute_claimed_job(claim_due_jobs()[0])

        job.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.CONNECTION_REQUIRED)
        self.assertEqual(self.variant.status, SocialPostState.CONNECTION_REQUIRED)

    def test_webhook_is_signature_checked_idempotent_and_replay_rejected(self):
        job = self.schedule()
        execute_claimed_job(claim_due_jobs()[0])
        job.refresh_from_db()
        body = json.dumps({
            "event_id": "provider-event-1",
            "event_type": "post.published",
            "outcome": "PUBLISHED",
            "external_id": job.external_id,
            "profile_id": self.connection.provider_profile_id,
            "account_id": self.connection.provider_account_id,
        }).encode()

        events = process_provider_webhook(
            provider=ProviderName.UPLOAD_POST,
            request=WebhookRequest(
                headers={"X-Fake-Signature": "valid-fake-signature"},
                body=body,
            ),
        )

        job.refresh_from_db()
        self.assertEqual(job.status, PublishJobState.PUBLISHED)
        self.assertEqual(len(events), 1)
        self.assertEqual(ProviderEvent.objects.count(), 1)
        with self.assertRaises(ProviderWebhookReplayError):
            process_provider_webhook(
                provider=ProviderName.UPLOAD_POST,
                request=WebhookRequest(
                    headers={"X-Fake-Signature": "valid-fake-signature"},
                    body=body,
                ),
            )
        with self.assertRaises(ProviderAuthenticationError):
            process_provider_webhook(
                provider=ProviderName.UPLOAD_POST,
                request=WebhookRequest(headers={}, body=body),
            )

        unscoped_body = json.dumps({
            "event_id": "provider-event-unscoped",
            "event_type": "post.published",
            "outcome": "PUBLISHED",
            "external_id": job.external_id,
        }).encode()
        with self.assertRaises(ProviderAuthenticationError):
            process_provider_webhook(
                provider=ProviderName.UPLOAD_POST,
                request=WebhookRequest(
                    headers={"X-Fake-Signature": "valid-fake-signature"},
                    body=unscoped_body,
                ),
            )

    def test_job_uses_snapshotted_provider_after_workspace_switch(self):
        job = self.schedule()
        zernio_connection = SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.ZERNIO,
            provider_profile_id="zernio-profile",
            provider_account_id="zernio-account",
            display_name="Zernio Company",
            account_type=SocialAccountType.ORGANIZATION,
            status=ConnectionState.CONNECTED,
        )
        self.social_settings.publishing_provider_override = SocialProvider.ZERNIO
        self.social_settings.save()
        zernio = FakeZernioProvider()
        created_for = []

        def provider_for(name):
            created_for.append(name)
            return self.fake if name == ProviderName.UPLOAD_POST else zernio

        with patch(
            "integrations.social.services.lifecycle.publishing_provider_registry.create",
            side_effect=provider_for,
        ):
            execute_claimed_job(claim_due_jobs()[0])

        job.refresh_from_db()
        self.assertEqual(job.provider, SocialProvider.UPLOAD_POST)
        self.assertEqual(job.connection, self.connection)
        self.assertEqual(created_for, [ProviderName.UPLOAD_POST])
        self.assertNotEqual(job.connection, zernio_connection)
