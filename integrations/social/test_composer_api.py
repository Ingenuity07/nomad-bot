from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.social.models import (
    ConnectionState,
    ContentSource,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialProvider,
)
from prospecting.models import Workspace, WorkspaceMembership


@override_settings(CONTENT_AUTOMATION_DEV_BOOTSTRAP=False)
class SocialComposerApiTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.user = users.objects.create_user(username="composer-user")
        self.other_user = users.objects.create_user(username="other-composer-user")
        self.workspace = Workspace.objects.create(name="Composer workspace")
        self.other_workspace = Workspace.objects.create(name="Other composer workspace")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.user, role=WorkspaceMembership.OWNER, is_active=True)
        WorkspaceMembership.objects.create(workspace=self.other_workspace, user=self.other_user, role=WorkspaceMembership.OWNER, is_active=True)
        self.connections = {}
        for network in SocialNetwork.values:
            self.connections[network] = SocialConnection.objects.create(
                workspace=self.workspace,
                network=network,
                provider=SocialProvider.UPLOAD_POST,
                provider_profile_id=f"profile-{network.lower()}",
                provider_account_id=f"account-{network.lower()}",
                display_name=f"{network.title()} account",
                status=ConnectionState.CONNECTED,
                connected_at=timezone.now(),
            )
        self.source = ContentSource.objects.create(workspace=self.workspace, label="Saved insight", text_content="A reusable customer insight")
        self.other_source = ContentSource.objects.create(workspace=self.other_workspace, label="Private insight", text_content="Private")
        self.other_post = SocialPost.objects.create(workspace=self.other_workspace, idea_title="Private draft")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def payload(self, **changes):
        value = {
            "idea_title": "Better handoffs",
            "idea_text": "Make each next step visible to the team.",
            "networks": ["LINKEDIN", "X", "INSTAGRAM"],
            "controls": {"tone": "Friendly", "goal": "Education", "length": "Medium", "include_image": True},
        }
        value.update(changes)
        return value

    def test_options_include_only_active_workspace_sources_connections_and_drafts(self):
        SocialPost.objects.create(workspace=self.workspace, idea_title="My draft")
        response = self.client.get(reverse("social-composer-options"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["network"] for item in response.data["connections"]}, set(SocialNetwork.values))
        self.assertEqual([item["label"] for item in response.data["sources"]], ["Saved insight"])
        self.assertEqual([item["idea_title"] for item in response.data["drafts"]], ["My draft"])
        self.assertNotIn("provider", response.data["connections"][0])
        self.assertNotContains(response, "Private insight")

    @patch("integrations.social.services.composer.SocialContentGenerator.generate")
    def test_generate_creates_a_distinct_variant_for_each_network(self, generate):
        generate.return_value = {
            "LINKEDIN": {"copy": "LinkedIn version", "hashtags": ["#Teams"], "metadata": {}},
            "X": {"copy": "X version", "hashtags": ["#Work"], "metadata": {}},
            "INSTAGRAM": {"copy": "Instagram version", "hashtags": ["#People"], "metadata": {}},
        }
        response = self.client.post(reverse("social-post-generate"), self.payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["variants"]), 3)
        self.assertEqual(len({item["copy"] for item in response.data["variants"]}), 3)
        self.assertEqual(SocialPostVariant.objects.filter(post_id=response.data["id"]).count(), 3)
        generated_variants = SocialPostVariant.objects.filter(post_id=response.data["id"]).prefetch_related("versions")
        self.assertTrue(all(item.versions.count() == 1 for item in generated_variants))
        self.assertTrue(all(item.versions.first().quality_check.get("schema_version") == 1 for item in generated_variants))
        self.assertNotContains(response, "UPLOAD_POST")

    def test_saved_source_and_existing_draft_are_supported(self):
        created = self.client.post(reverse("social-post-list"), self.payload(source_id=str(self.source.id), idea_text=""), format="json")
        self.assertEqual(created.status_code, 201)
        loaded = self.client.get(reverse("social-post-detail", args=[created.data["id"]]))
        self.assertEqual(loaded.data["source"]["id"], str(self.source.id))
        self.assertEqual(loaded.data["idea_title"], "Better handoffs")

    def test_edit_autosave_shape_and_rewrite_actions(self):
        created = self.client.post(reverse("social-post-list"), self.payload(networks=["X"]), format="json")
        variant_id = created.data["variants"][0]["id"]
        edited = self.client.patch(reverse("social-variant-detail", args=[variant_id]), {"copy": "One. Two. Three.", "hashtags": ["#One"]}, format="json")
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.data["variants"][0]["copy"], "One. Two. Three.")
        rewritten = self.client.post(reverse("social-variant-rewrite", args=[variant_id]), {"action": "CREATE_X_THREAD"}, format="json")
        self.assertEqual(rewritten.status_code, 200)
        self.assertEqual(rewritten.data["variants"][0]["metadata"]["format"], "THREAD")
        self.assertGreaterEqual(len(rewritten.data["variants"][0]["metadata"]["thread"]), 2)

    def test_network_selection_adds_and_removes_draft_variants_only(self):
        created = self.client.post(reverse("social-post-list"), self.payload(networks=["LINKEDIN", "X"]), format="json")
        post_id = created.data["id"]
        removed = self.client.patch(reverse("social-post-detail", args=[post_id]), {"networks": ["LINKEDIN"]}, format="json")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual([item["network"] for item in removed.data["variants"]], ["LINKEDIN"])
        linkedin = SocialPostVariant.objects.get(post_id=post_id)
        linkedin.status = SocialPostState.NEEDS_REVIEW
        linkedin.save(update_fields=["status"])
        protected = self.client.patch(reverse("social-post-detail", args=[post_id]), {"networks": ["X"]}, format="json")
        self.assertEqual(protected.status_code, 400)
        self.assertTrue(SocialPostVariant.objects.filter(pk=linkedin.id).exists())

    def test_plain_platform_validation_blocks_review_then_allows_valid_post(self):
        created = self.client.post(reverse("social-post-list"), self.payload(networks=["X"]), format="json")
        post_id = created.data["id"]
        variant_id = created.data["variants"][0]["id"]
        invalid = self.client.post(reverse("social-post-submit-review", args=[post_id]), {}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Write the post", str(invalid.data))
        self.client.patch(reverse("social-variant-detail", args=[variant_id]), {"copy": "A concise X post."}, format="json")
        submitted = self.client.post(reverse("social-post-submit-review", args=[post_id]), {}, format="json")
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.data["state"], SocialPostState.NEEDS_REVIEW)

    def test_workspace_cannot_read_modify_or_reference_another_workspace_content(self):
        read = self.client.get(reverse("social-post-detail", args=[self.other_post.id]))
        modify = self.client.patch(reverse("social-post-detail", args=[self.other_post.id]), {"idea_title": "Stolen"}, format="json")
        source = self.client.post(reverse("social-post-list"), self.payload(source_id=str(self.other_source.id)), format="json")
        forbidden = self.client.get(reverse("social-composer-options"), HTTP_X_WORKSPACE_ID=str(self.other_workspace.id))
        self.assertEqual(read.status_code, 404)
        self.assertEqual(modify.status_code, 404)
        self.assertEqual(source.status_code, 400)
        self.assertEqual(forbidden.status_code, 403)
        self.other_post.refresh_from_db()
        self.assertEqual(self.other_post.idea_title, "Private draft")

    def test_unauthenticated_requests_return_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse("social-composer-options"))
        self.assertEqual(response.status_code, 401)

    @override_settings(CONTENT_STUDIO_DRAFT_ONLY_ALLOWED=True)
    def test_draft_only_mode_can_create_linkedin_copy_but_cannot_submit_it(self):
        SocialConnection.objects.filter(workspace=self.workspace).delete()
        options = self.client.get(reverse("social-composer-options"))
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.data["connections"][0]["display_name"], "Draft only")
        created = self.client.post(reverse("social-post-list"), self.payload(networks=["LINKEDIN"]), format="json")
        self.assertEqual(created.status_code, 201)
        self.assertIsNone(created.data["variants"][0]["account"])
        variant_id = created.data["variants"][0]["id"]
        self.client.patch(reverse("social-variant-detail", args=[variant_id]), {"copy": "A draft for later."}, format="json")
        submitted = self.client.post(reverse("social-post-submit-review", args=[created.data["id"]]), {}, format="json")
        self.assertEqual(submitted.status_code, 400)
        self.assertIn("Reconnect LinkedIn", str(submitted.data))
