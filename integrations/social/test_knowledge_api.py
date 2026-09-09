from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.social.models import (
    BrandProfile,
    ConnectionState,
    ContentSource,
    ContentSourceProcessingState,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialProvider,
)
from integrations.social.services.knowledge import record_voice_edit
from prospecting.models import Workspace, WorkspaceMembership


@override_settings(CONTENT_AUTOMATION_DEV_BOOTSTRAP=False)
class ContentKnowledgeApiTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.user = users.objects.create_user(username="knowledge-user")
        self.other = users.objects.create_user(username="other-knowledge-user")
        self.workspace = Workspace.objects.create(name="Knowledge workspace")
        self.other_workspace = Workspace.objects.create(name="Private knowledge")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.user, role=WorkspaceMembership.OWNER, is_active=True)
        WorkspaceMembership.objects.create(workspace=self.other_workspace, user=self.other, role=WorkspaceMembership.OWNER, is_active=True)
        self.connection = SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.UPLOAD_POST,
            provider_profile_id="profile",
            provider_account_id="account",
            display_name="Knowledge Page",
            status=ConnectionState.CONNECTED,
            connected_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_brand_brain_is_versioned_and_generation_records_the_version(self):
        initial = self.client.get(reverse("social-brand-brain"))
        updated = self.client.put(reverse("social-brand-brain"), {
            "business_description": "We help support teams learn from customer conversations.",
            "audience": "Customer success leaders",
            "goals": ["Earn trust"],
            "voice": "Clear and candid",
            "voice_rules": ["Use concrete examples"],
            "example_posts": ["A lesson from last week"],
            "content_pillars": ["Customer learning"],
            "calls_to_action": ["Share your experience"],
            "visual_direction": "Documentary photography",
            "forbidden_topics": ["Unverified results"],
        }, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["version"], initial.data["version"] + 1)
        with patch("integrations.social.services.composer.SocialContentGenerator.generate", return_value={
            "LINKEDIN": {"copy": "A grounded lesson", "hashtags": [], "metadata": {}},
        }):
            generated = self.client.post(reverse("social-post-generate"), {
                "idea_title": "A grounded lesson", "idea_text": "Useful context", "networks": ["LINKEDIN"], "controls": {},
            }, format="json")
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated.data["brand_brain_version"]["version"], updated.data["version"])
        self.assertEqual(generated.data["variants"][0]["metadata"]["brand_brain_version"]["version"], updated.data["version"])

    def test_repeated_edits_only_apply_a_voice_rule_after_confirmation(self):
        profile_response = self.client.get(reverse("social-brand-brain"))
        profile = BrandProfile.objects.get(pk=profile_response.data["id"])
        before = "This is a long and repetitive sentence. " * 8
        for _ in range(3):
            record_voice_edit(workspace=self.workspace, before=before, after="A concise lesson.")
        profile.refresh_from_db()
        self.assertNotIn("Prefer concise posts and remove repetition.", profile.voice_rules)
        suggestions = self.client.get(reverse("social-brand-brain")).data["suggestions"]
        self.assertEqual(len(suggestions), 1)
        confirmed = self.client.post(reverse("social-brand-voice-suggestion", args=[suggestions[0]["id"]]), {"action": "CONFIRM"}, format="json")
        self.assertEqual(confirmed.status_code, 200)
        self.assertIn("Prefer concise posts and remove repetition.", confirmed.data["voice_rules"])
        self.assertEqual(confirmed.data["version"], profile_response.data["version"] + 1)

    def test_sources_are_owned_safe_and_traceable_across_multi_source_generation(self):
        text = self.client.post(reverse("social-content-sources"), {"source_type": "TEXT", "label": "Research note", "text_content": "Customers need clear next steps."}, format="json")
        transcript = self.client.post(reverse("social-content-sources"), {"source_type": "VOICE_NOTE", "label": "Founder note", "text_content": "We learned to explain the next action."}, format="json")
        pending = self.client.post(reverse("social-content-sources"), {"source_type": "URL", "label": "Public article", "source_url": "https://example.com/article"}, format="json")
        blocked = self.client.post(reverse("social-content-sources"), {"source_type": "URL", "source_url": "http://127.0.0.1/private"}, format="json")
        self.assertEqual(text.data["processing_status"], ContentSourceProcessingState.READY)
        self.assertEqual(text.data["owner_name"], self.user.username)
        self.assertEqual(pending.data["processing_status"], ContentSourceProcessingState.PENDING)
        self.assertEqual(pending.data["text_content"], "")
        self.assertEqual(blocked.status_code, 400)
        with patch("integrations.social.services.composer.SocialContentGenerator.generate", return_value={
            "LINKEDIN": {"copy": "Only supported claims", "hashtags": [], "metadata": {}},
        }):
            generated = self.client.post(reverse("social-post-generate"), {
                "idea_title": "Supported claims", "source_ids": [text.data["id"], transcript.data["id"]], "networks": ["LINKEDIN"], "controls": {},
            }, format="json")
        references = generated.data["variants"][0]["metadata"]["source_references"]
        self.assertEqual({item["label"] for item in references}, {"Research note", "Founder note"})
        submitted = self.client.post(reverse("social-post-submit-review", args=[generated.data["id"]]), {}, format="json")
        self.assertEqual(submitted.status_code, 200)
        ContentSource.objects.filter(pk=text.data["id"]).update(is_active=False)
        review = self.client.get(reverse("social-approvals"))
        review_sources = review.data["NEEDS_REVIEW"][0]["versions"][0]["metadata"]["source_references"]
        availability = {item["label"]: item["available"] for item in review_sources}
        self.assertFalse(availability["Research note"])
        self.assertTrue(availability["Founder note"])
        unavailable = self.client.post(reverse("social-post-generate"), {
            "idea_title": "Unavailable source", "source_ids": [pending.data["id"]], "networks": ["LINKEDIN"], "controls": {},
        }, format="json")
        self.assertEqual(unavailable.status_code, 400)
        self.assertIn("not ready", str(unavailable.data))
        private = ContentSource.objects.create(workspace=self.other_workspace, label="Private", text_content="Secret")
        isolated = self.client.post(reverse("social-post-list"), {"idea_title": "No", "source_ids": [str(private.id)], "networks": ["LINKEDIN"]}, format="json")
        self.assertEqual(isolated.status_code, 400)

    def test_weekly_story_interview_adapts_and_approved_answers_become_a_source(self):
        current = self.client.get(reverse("social-story-interview"))
        self.assertEqual(len(current.data["questions"]), 3)
        saved = self.client.patch(reverse("social-story-interview"), {"answers": {
            "moment": "A customer found a simpler way to onboard their team.",
            "why": "It removed uncertainty from the first week.",
        }}, format="json")
        self.assertEqual(saved.status_code, 200)
        self.assertIn("customer_voice", [item["id"] for item in saved.data["questions"]])
        approved = self.client.post(reverse("social-story-interview"), {"action": "APPROVE"}, format="json")
        self.assertEqual(approved.status_code, 200)
        source = ContentSource.objects.get(pk=approved.data["approved_source_id"])
        self.assertEqual(source.owner, self.user)
        self.assertEqual(source.processing_status, ContentSourceProcessingState.READY)
        self.assertIn("simpler way", source.extracted_text)

    def test_knowledge_endpoints_do_not_cross_workspace_boundaries(self):
        private = ContentSource.objects.create(workspace=self.other_workspace, label="Private source", text_content="Private")
        listed = self.client.get(reverse("social-content-sources"))
        self.assertNotIn(str(private.id), str(listed.data))
        self.assertFalse(SocialPost.objects.filter(workspace=self.other_workspace).exists())
