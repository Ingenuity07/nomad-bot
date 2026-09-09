from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.linkedin.models import LinkedInAutomationSettings, LinkedInPost
from integrations.social.models import ConnectionState, ContentStudioOnboarding, SocialConnection, SocialNetwork, SocialProvider
from integrations.social.publishing.adapters import TARGET_CAPABILITIES
from integrations.social.publishing.fakes import FakeUploadPostProvider
from integrations.social.publishing.types import PublishingNetwork, SocialAccount
from prospecting.models import Workspace, WorkspaceMembership


@override_settings(CONTENT_STUDIO_DRAFT_ONLY_ALLOWED=True, SOCIAL_PUBLISHER_DEFAULT="UPLOAD_POST")
class ContentStudioOnboardingApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="onboarding-user", password="test-password")
        self.workspace = Workspace.objects.create(name="Onboarding workspace")
        WorkspaceMembership.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=WorkspaceMembership.OWNER,
            is_active=True,
        )
        self.client.force_login(self.user)
        self.fake_account = SocialAccount(
            provider_profile_id="profile-1",
            provider_account_id="page-1",
            network=PublishingNetwork.LINKEDIN,
            display_name="LumaDesk Company Page",
            account_type="ORGANIZATION",
            capabilities=TARGET_CAPABILITIES,
        )
        self.fake = FakeUploadPostProvider(accounts=(self.fake_account,))

    @staticmethod
    def connection_state(response):
        return parse_qs(urlparse(response.data["authorization_url"]).query)["state"][0]

    def test_first_use_is_not_started_and_can_enter_draft_only_mode(self):
        response = self.client.get(reverse("content-studio-onboarding"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "NOT_STARTED")
        self.assertEqual(response.data["current_step"], 1)
        self.assertTrue(response.data["can_skip_connection"])
        self.assertEqual([item["network"] for item in response.data["networks"]], ["LINKEDIN", "X", "INSTAGRAM"])

        completed = self.client.post(
            reverse("content-studio-onboarding-step", args=[1]),
            {"skip": True},
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        self.assertTrue(completed.data["draft_only_mode"])
        self.assertIn(1, completed.data["completed_steps"])

    def test_established_workspace_is_not_forced_through_first_run(self):
        LinkedInAutomationSettings.objects.create(
            workspace=self.workspace,
            page_name="Established Business",
            company_description="Existing saved setup",
        )
        response = self.client.get(reverse("content-studio-onboarding"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "COMPLETE")
        self.assertEqual(response.data["completed_steps"], [1, 2, 3, 4])

    def test_back_navigation_and_refresh_recover_saved_answers(self):
        self.client.post(reverse("content-studio-onboarding"), {}, content_type="application/json")
        self.client.post(reverse("content-studio-onboarding-step", args=[1]), {"skip": True}, content_type="application/json")
        saved = self.client.post(reverse("content-studio-onboarding-step", args=[2]), {
            "name": "Saved Business",
            "description": "We help operations teams.",
            "audience": "Operations leaders",
            "language": "English",
        }, content_type="application/json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.data["current_step"], 3)

        back = self.client.patch(reverse("content-studio-onboarding"), {"current_step": 2}, content_type="application/json")
        self.assertEqual(back.status_code, 200)
        refreshed = self.client.get(reverse("content-studio-onboarding"))
        self.assertEqual(refreshed.data["current_step"], 2)
        self.assertEqual(refreshed.data["business"]["name"], "Saved Business")
        self.assertEqual(refreshed.data["business"]["audience"], "Operations leaders")

    def test_topics_and_schedule_are_saved_to_existing_content_settings(self):
        response = self.client.post(reverse("content-studio-onboarding-step", args=[3]), {
            "topics": ["Customer stories", "Product tips"],
            "posting_days": [1, 3, 5],
            "time": "14:30",
            "timezone": "Europe/London",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertIn(3, response.data["completed_steps"])
        self.assertEqual(response.data["schedule"]["topics"], ["Customer stories", "Product tips"])
        self.assertEqual(response.data["schedule"]["posting_days"], [1, 3, 5])
        legacy = LinkedInAutomationSettings.objects.get(workspace=self.workspace)
        self.assertEqual(legacy.post_time.strftime("%H:%M"), "14:30")
        self.assertEqual(legacy.timezone, "Europe/London")
        self.assertEqual(legacy.workspace.social_content_settings.schedule_days, [1, 3, 5])

    @patch("integrations.social.services.onboarding.publishing_provider_registry.create")
    def test_successful_connection_returns_safe_account_details(self, create_provider):
        create_provider.return_value = self.fake
        started = self.client.post(reverse("content-studio-connection-start"), {}, content_type="application/json")
        self.assertEqual(started.status_code, 200)
        self.assertIn("authorization_url", started.data)
        self.assertNotIn("provider", started.data)
        state = self.connection_state(started)
        self.assertNotEqual(
            ContentStudioOnboarding.objects.get(workspace=self.workspace).connection_state,
            state,
        )

        completed = self.client.post(reverse("content-studio-connection-complete"), {
            "state": state,
            "code": "authorization-code",
        }, content_type="application/json")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.data["connection"]["display_name"], "LumaDesk Company Page")
        self.assertEqual(completed.data["connection"]["account_type"], "Company Page")
        self.assertEqual(completed.data["connection"]["health"], "HEALTHY")
        self.assertNotIn("provider", completed.data)
        connection = SocialConnection.objects.get(provider_account_id="page-1")
        self.assertEqual(connection.provider, SocialProvider.UPLOAD_POST)

    @patch("integrations.social.services.onboarding.publishing_provider_registry.create")
    def test_connection_cancellation_is_resumable(self, create_provider):
        create_provider.return_value = self.fake
        self.client.post(reverse("content-studio-connection-start"), {}, content_type="application/json")
        cancelled = self.client.post(reverse("content-studio-connection-cancel"), {}, content_type="application/json")
        self.assertEqual(cancelled.status_code, 200)
        self.assertIn("cancelled", cancelled.data["connection"]["message"].lower())
        self.assertEqual(cancelled.data["current_step"], 1)

    @patch("integrations.social.services.onboarding.publishing_provider_registry.create")
    def test_expired_connection_shows_reconnect_explanation(self, create_provider):
        create_provider.return_value = self.fake
        started = self.client.post(reverse("content-studio-connection-start"), {}, content_type="application/json")
        onboarding = ContentStudioOnboarding.objects.get(workspace=self.workspace)
        state = self.connection_state(started)
        onboarding.connection_expires_at = timezone.now() - timedelta(seconds=1)
        onboarding.save(update_fields=["connection_expires_at"])
        failed = self.client.post(reverse("content-studio-connection-complete"), {
            "state": state, "code": "late-code",
        }, content_type="application/json")
        self.assertEqual(failed.status_code, 400)
        refreshed = self.client.get(reverse("content-studio-onboarding"))
        self.assertIn("Reconnect", refreshed.data["connection"]["message"])

    @patch("integrations.social.services.onboarding.publishing_provider_registry.create")
    def test_reconnect_replaces_unhealthy_state_with_connected_account(self, create_provider):
        create_provider.return_value = self.fake
        SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.UPLOAD_POST,
            provider_profile_id="old-profile",
            provider_account_id="old-page",
            display_name="Expired Page",
            status=ConnectionState.REVOKED,
        )
        before = self.client.get(reverse("content-studio-onboarding"))
        self.assertEqual(before.data["connection"]["health"], "NEEDS_ATTENTION")
        started = self.client.post(reverse("content-studio-connection-start"), {}, content_type="application/json")
        state = self.connection_state(started)
        completed = self.client.post(reverse("content-studio-connection-complete"), {
            "state": state, "code": "new-code",
        }, content_type="application/json")
        self.assertEqual(completed.data["connection"]["health"], "HEALTHY")
        self.assertEqual(completed.data["connection"]["display_name"], "LumaDesk Company Page")

    def test_finishing_with_first_workspace_post_completes_checklist(self):
        settings = LinkedInAutomationSettings.objects.create(workspace=self.workspace)
        post = LinkedInPost.objects.create(
            settings=settings,
            topic="First post",
            body="First draft",
            scheduled_for=timezone.now(),
        )
        response = self.client.post(reverse("content-studio-onboarding-step", args=[4]), {
            "post_id": str(post.id),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "COMPLETE")
        self.assertEqual(response.data["first_post_id"], str(post.id))

    @override_settings(CONTENT_STUDIO_DRAFT_ONLY_ALLOWED=False)
    def test_skip_is_hidden_and_rejected_when_draft_only_mode_is_disabled(self):
        response = self.client.get(reverse("content-studio-onboarding"))
        self.assertFalse(response.data["can_skip_connection"])
        skipped = self.client.post(reverse("content-studio-onboarding-step", args=[1]), {"skip": True}, content_type="application/json")
        self.assertEqual(skipped.status_code, 400)

    def test_onboarding_is_workspace_isolated(self):
        other = Workspace.objects.create(name="Other workspace")
        response = self.client.get(reverse("content-studio-onboarding"), HTTP_X_WORKSPACE_ID=str(other.id))
        self.assertEqual(response.status_code, 403)

    @override_settings(CONTENT_AUTOMATION_DEV_BOOTSTRAP=False)
    def test_onboarding_requires_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("content-studio-onboarding"))
        self.assertEqual(response.status_code, 401)
