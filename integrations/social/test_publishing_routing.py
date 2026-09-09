from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.social.models import (
    ConnectionState,
    PublishJob,
    SocialAccountType,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostVariant,
    SocialPostVersion,
    SocialProvider,
    SocialWorkspaceSettings,
)
from integrations.social.publishing.types import ProviderName, RoutingOutcome
from integrations.social.services.publishing_routing import (
    SocialPublisherAdminService,
    create_publish_job,
    create_social_connection,
    route_social_account,
    selected_provider,
)
from prospecting.models import Workspace, WorkspaceMembership


@override_settings(
    SOCIAL_PUBLISHER_DEFAULT="UPLOAD_POST",
    UPLOAD_POST_API_KEY="",
    UPLOAD_POST_WEBHOOK_SECRET="",
    UPLOAD_POST_CONNECTION_RETURN_URL="",
)
class PublishingRoutingPolicyTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="publisher-owner")
        self.member = user_model.objects.create_user(username="publisher-member")
        self.staff = user_model.objects.create_user(username="publisher-staff", is_staff=True)
        self.workspace = Workspace.objects.create(name="Publisher routing")
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
        self.social_settings = SocialWorkspaceSettings.objects.create(workspace=self.workspace)

    def create_connection(self, provider, account_id="account-1", status=ConnectionState.CONNECTED):
        return SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=provider,
            provider_profile_id="profile-1",
            provider_account_id=account_id,
            display_name="Example Page",
            account_type=SocialAccountType.ORGANIZATION,
            status=status,
        )

    def create_variant_and_version(self, suffix="one"):
        post = SocialPost.objects.create(
            workspace=self.workspace,
            idea_title=f"Post {suffix}",
        )
        variant = SocialPostVariant.objects.create(
            post=post,
            network=SocialNetwork.LINKEDIN,
            copy=f"Copy {suffix}",
            scheduled_for=timezone.now(),
        )
        version = SocialPostVersion.objects.create(
            variant=variant,
            version=1,
            copy=variant.copy,
            approved_at=timezone.now(),
        )
        return variant, version

    def test_global_default_routes_new_connections_and_jobs(self):
        connection_result = create_social_connection(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider_profile_id="profile-1",
            provider_account_id="account-1",
            display_name="Example Page",
            account_type=SocialAccountType.ORGANIZATION,
        )
        variant, version = self.create_variant_and_version()
        job_result = create_publish_job(
            variant=variant,
            approved_version=version,
            idempotency_key="global-default-job",
            provider_account_id="account-1",
        )

        self.assertEqual(connection_result.provider, ProviderName.UPLOAD_POST)
        self.assertEqual(
            SocialConnection.objects.get(pk=connection_result.connection_id).provider,
            SocialProvider.UPLOAD_POST,
        )
        self.assertEqual(job_result.outcome, RoutingOutcome.READY)
        self.assertEqual(PublishJob.objects.get(pk=job_result.publish_job_id).provider, SocialProvider.UPLOAD_POST)

    @override_settings(SOCIAL_PUBLISHER_DEFAULT="ZERNIO")
    def test_global_default_can_route_to_zernio(self):
        self.assertEqual(selected_provider(self.workspace), ProviderName.ZERNIO)

    def test_workspace_admin_override_wins_and_member_cannot_change_it(self):
        SocialPublisherAdminService.set_workspace_override(
            actor=self.owner,
            workspace=self.workspace,
            provider=ProviderName.ZERNIO,
        )

        self.assertEqual(selected_provider(self.workspace), ProviderName.ZERNIO)
        with self.assertRaises(PermissionDenied):
            SocialPublisherAdminService.set_workspace_override(
                actor=self.member,
                workspace=self.workspace,
                provider=ProviderName.UPLOAD_POST,
            )

    def test_disabled_selected_provider_is_not_routed(self):
        self.create_connection(SocialProvider.UPLOAD_POST)
        SocialPublisherAdminService.set_provider_enabled(
            actor=self.owner,
            workspace=self.workspace,
            provider=ProviderName.UPLOAD_POST,
            enabled=False,
        )

        result = route_social_account(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider_account_id="account-1",
        )

        self.assertEqual(result.outcome, RoutingOutcome.PROVIDER_DISABLED)

    def test_missing_or_inactive_connection_returns_connection_required(self):
        self.create_connection(SocialProvider.UPLOAD_POST, status=ConnectionState.DISCONNECTED)
        variant, version = self.create_variant_and_version()

        result = create_publish_job(
            variant=variant,
            approved_version=version,
            idempotency_key="connection-required-job",
            provider_account_id="account-1",
        )

        self.assertEqual(result.outcome, RoutingOutcome.CONNECTION_REQUIRED)
        self.assertFalse(PublishJob.objects.filter(idempotency_key="connection-required-job").exists())

    def test_provider_change_only_affects_new_jobs(self):
        self.create_connection(SocialProvider.UPLOAD_POST, account_id="upload-account")
        first_variant, first_version = self.create_variant_and_version("first")
        first = create_publish_job(
            variant=first_variant,
            approved_version=first_version,
            idempotency_key="scheduled-before-change",
            provider_account_id="upload-account",
        )

        SocialPublisherAdminService.set_workspace_override(
            actor=self.owner,
            workspace=self.workspace,
            provider=ProviderName.ZERNIO,
        )
        self.create_connection(SocialProvider.ZERNIO, account_id="zernio-account")
        second_variant, second_version = self.create_variant_and_version("second")
        second = create_publish_job(
            variant=second_variant,
            approved_version=second_version,
            idempotency_key="scheduled-after-change",
            provider_account_id="zernio-account",
        )
        idempotent_retry = create_publish_job(
            variant=first_variant,
            approved_version=first_version,
            idempotency_key="scheduled-before-change",
            provider_account_id="upload-account",
        )

        first_job = PublishJob.objects.get(pk=first.publish_job_id)
        second_job = PublishJob.objects.get(pk=second.publish_job_id)
        self.assertEqual(first_job.provider, SocialProvider.UPLOAD_POST)
        self.assertEqual(second_job.provider, SocialProvider.ZERNIO)
        self.assertEqual(idempotent_retry.provider, ProviderName.UPLOAD_POST)
        self.assertEqual(idempotent_retry.publish_job_id, first_job.id)

    def test_internal_health_endpoint_is_staff_only_and_secret_free(self):
        client = APIClient()
        client.force_authenticate(self.member)
        forbidden = client.get(reverse("social-publisher-health"))
        self.assertEqual(forbidden.status_code, 403)
        self.assertNotIn("UPLOAD_POST", forbidden.content.decode())
        self.assertNotIn("ZERNIO", forbidden.content.decode())

        client.force_authenticate(self.staff)
        response = client.get(reverse("social-publisher-health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(
            {row["provider"] for row in response.data["providers"]},
            {"UPLOAD_POST", "ZERNIO"},
        )
        self.assertNotIn("secret", response.content.decode().lower())
