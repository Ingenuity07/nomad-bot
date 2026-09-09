from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.social.models import (
    ConnectionState,
    ContentSource,
    ProviderEvent,
    PublishJob,
    PublishJobState,
    SocialAccountType,
    SocialAuditEvent,
    SocialAuditEventType,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostVariant,
    SocialPostVersion,
    SocialProvider,
)
from integrations.social.services.audit import record_audit_event
from prospecting.models import Workspace, WorkspaceMembership


class ContentStudioProductionReadinessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="readiness-owner")
        self.other_owner = user_model.objects.create_user(username="readiness-other")
        self.member = user_model.objects.create_user(username="readiness-member")
        self.staff = user_model.objects.create_user(username="readiness-staff", is_staff=True)
        self.workspace = Workspace.objects.create(name="Readiness workspace")
        self.other_workspace = Workspace.objects.create(name="Private workspace")
        WorkspaceMembership.objects.create(
            workspace=self.workspace,
            user=self.owner,
            role=WorkspaceMembership.OWNER,
            is_active=True,
        )
        WorkspaceMembership.objects.create(
            workspace=self.workspace,
            user=self.member,
            role=WorkspaceMembership.MEMBER,
        )
        WorkspaceMembership.objects.create(
            workspace=self.other_workspace,
            user=self.other_owner,
            role=WorkspaceMembership.OWNER,
            is_active=True,
        )
        ContentSource.objects.create(
            workspace=self.workspace,
            owner=self.owner,
            label="Owner source",
            text_content="Export this supported statement.",
        )
        ContentSource.objects.create(
            workspace=self.other_workspace,
            owner=self.other_owner,
            label="Other secret source",
            text_content="Never cross workspace boundaries.",
        )
        self.client = APIClient()

    def _connection_and_job(self, *, status=PublishJobState.SCHEDULED):
        connection = SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.UPLOAD_POST,
            provider_profile_id="sensitive-profile",
            provider_account_id="sensitive-account",
            display_name="Example Page",
            account_type=SocialAccountType.ORGANIZATION,
            status=ConnectionState.CONNECTED,
        )
        post = SocialPost.objects.create(workspace=self.workspace, idea_title="Export me")
        variant = SocialPostVariant.objects.create(
            post=post,
            connection=connection,
            network=SocialNetwork.LINKEDIN,
            copy="Customer copy",
            scheduled_for=timezone.now() + timedelta(hours=1),
        )
        version = SocialPostVersion.objects.create(
            variant=variant,
            version=1,
            copy=variant.copy,
            scheduled_for=variant.scheduled_for,
        )
        job = PublishJob.objects.create(
            variant=variant,
            connection=connection,
            approved_version=version,
            provider=SocialProvider.UPLOAD_POST,
            idempotency_key=f"readiness-{status}",
            status=status,
            scheduled_for=variant.scheduled_for,
        )
        return connection, post, job

    def test_export_is_admin_only_scoped_and_hides_vendor_identifiers(self):
        self._connection_and_job()
        record_audit_event(
            workspace=self.workspace,
            event_type=SocialAuditEventType.PROVIDER_POLICY_CHANGED,
            actor=self.owner,
            details={"provider": "UPLOAD_POST", "provider_account_id": "sensitive-account"},
        )
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(reverse("social-data-export")).status_code, 403)

        self.client.force_authenticate(self.owner)
        inaccessible = self.client.get(
            reverse("social-data-export"),
            HTTP_X_WORKSPACE_ID=str(self.other_workspace.id),
        )
        self.assertEqual(inaccessible.status_code, 403)
        response = self.client.get(reverse("social-data-export"))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Owner source", body)
        self.assertNotIn("Other secret source", body)
        self.assertNotIn("sensitive-profile", body)
        self.assertNotIn("sensitive-account", body)
        self.assertNotIn("UPLOAD_POST", body)

    def test_deletion_requires_owner_confirmation_and_blocks_inflight_publish(self):
        _connection, post, job = self._connection_and_job(status=PublishJobState.PUBLISHING)
        self.client.force_authenticate(self.member)
        denied = self.client.delete(
            reverse("social-data-delete"),
            {"confirmation": "DELETE CONTENT STUDIO"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.owner)
        unconfirmed = self.client.delete(
            reverse("social-data-delete"),
            {"confirmation": "delete"},
            format="json",
        )
        self.assertEqual(unconfirmed.status_code, 400)
        blocked = self.client.delete(
            reverse("social-data-delete"),
            {"confirmation": "DELETE CONTENT STUDIO"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(SocialPost.objects.filter(pk=post.pk).exists())

        job.status = PublishJobState.FAILED
        job.save(update_fields=["status", "updated_at"])
        deleted = self.client.delete(
            reverse("social-data-delete"),
            {"confirmation": "DELETE CONTENT STUDIO"},
            format="json",
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(SocialPost.objects.filter(workspace=self.workspace).exists())
        self.assertFalse(SocialConnection.objects.filter(workspace=self.workspace).exists())
        self.assertTrue(SocialAuditEvent.objects.filter(
            workspace=self.workspace,
            event_type=SocialAuditEventType.DATA_DELETED,
        ).exists())
        self.assertTrue(ContentSource.objects.filter(workspace=self.other_workspace).exists())

    def test_audit_details_redact_credentials_and_are_immutable(self):
        event = record_audit_event(
            workspace=self.workspace,
            event_type=SocialAuditEventType.CONNECTION_STARTED,
            actor=self.owner,
            details={
                "access_token": "do-not-store",
                "authorization": "Bearer secret",
                "provider_account_id": "account-secret",
                "network": "LINKEDIN",
            },
        )
        self.assertEqual(event.details["access_token"], "<redacted>")
        self.assertEqual(event.details["provider_account_id"], "<redacted>")
        self.assertNotIn("do-not-store", str(event.details))
        event.details = {"changed": True}
        with self.assertRaisesMessage(Exception, "immutable"):
            event.save()

    @override_settings(
        SOCIAL_HEALTH_OVERDUE_JOB_MINUTES=5,
        SOCIAL_HEALTH_FAILED_JOB_THRESHOLD=1,
    )
    def test_internal_health_reports_queue_alerts_without_secrets(self):
        _connection, _post, job = self._connection_and_job(status=PublishJobState.FAILED)
        job.failure_message = "secret diagnostic should not be returned"
        job.save(update_fields=["failure_message", "updated_at"])
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("social-publisher-health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["operations"]["status"], "DEGRADED")
        self.assertIn(
            "FAILURE_SPIKE",
            {alert["code"] for alert in response.data["operations"]["alerts"]},
        )
        self.assertNotIn("secret diagnostic", response.content.decode())


class ProviderEventStorageTests(TestCase):
    def test_provider_event_payload_can_be_minimal_and_replay_key_is_unique(self):
        workspace = Workspace.objects.create(name="Provider event storage")
        ProviderEvent.objects.create(
            workspace=workspace,
            provider=SocialProvider.UPLOAD_POST,
            external_event_id="delivery-1",
            event_type="post.updated",
            payload={"status": "PUBLISHED"},
        )
        with self.assertRaises(IntegrityError):
            ProviderEvent.objects.create(
                workspace=workspace,
                provider=SocialProvider.UPLOAD_POST,
                external_event_id="delivery-1",
                event_type="post.updated",
                payload={},
            )
