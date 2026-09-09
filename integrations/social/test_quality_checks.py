from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.social.models import (
    BrandProfile,
    ConnectionState,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialProvider,
    SocialWorkspaceSettings,
)
from integrations.social.publishing.fakes import FakeUploadPostProvider
from integrations.social.services.lifecycle import create_version, edit_variant
from integrations.social.services.studio import approve_exact_version, serialize_version
from prospecting.models import Workspace


class AdvisoryAnalyzer:
    def analyze(self, **_kwargs):
        return {
            "voice_match": {"review": True, "explanation": "The tone may not match the saved voice rule."},
            "unsupported_claims": {"review": True, "explanation": "A result is stated without clear source support."},
            "copyright_attribution": {"review": True, "explanation": "A quotation may need attribution."},
        }


@override_settings(SOCIAL_QUALITY_LLM_ENABLED=False, SOCIAL_PUBLISHER_DEFAULT="UPLOAD_POST")
class QualityCheckTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="quality-reviewer")
        self.workspace = Workspace.objects.create(name="Quality workspace")
        self.settings = SocialWorkspaceSettings.objects.create(workspace=self.workspace, brand_name="Quality Co")
        self.brand_profile = BrandProfile.objects.create(settings=self.settings)
        self.connection = SocialConnection.objects.create(
            workspace=self.workspace,
            network=SocialNetwork.LINKEDIN,
            provider=SocialProvider.UPLOAD_POST,
            provider_profile_id="quality-profile",
            provider_account_id="quality-account",
            display_name="Quality Page",
            status=ConnectionState.CONNECTED,
        )

    def variant(self, *, copy="A useful and specific opening.\n\nA practical idea for teams.", network=SocialNetwork.LINKEDIN, connection=True):
        post = SocialPost.objects.create(workspace=self.workspace, idea_title="Quality idea", state=SocialPostState.DRAFT)
        return SocialPostVariant.objects.create(
            post=post,
            connection=self.connection if connection else None,
            network=network,
            copy=copy,
            hashtags=["#Teams"],
            scheduled_for=timezone.now() + timedelta(days=1),
            status=SocialPostState.DRAFT,
        )

    def test_report_is_stored_on_the_exact_immutable_version(self):
        variant = self.variant()
        original = variant.copy
        version = create_version(variant, quality_analyzer=AdvisoryAnalyzer())

        self.assertEqual(version.copy, original)
        self.assertEqual(version.quality_check["schema_version"], 1)
        self.assertFalse(version.quality_check["hard_blocked"])
        self.assertEqual(version.quality_check["summary"]["review"], 3)
        self.assertEqual(
            {item["key"] for item in version.quality_check["suggestions"]},
            {"voice_match", "unsupported_claims", "copyright_attribution"},
        )
        self.assertEqual(serialize_version(version)["quality_check"], version.quality_check)

    def test_only_required_deterministic_rules_hard_block(self):
        self.brand_profile.forbidden_topics = ["secret launch"]
        self.brand_profile.save(update_fields=["forbidden_topics", "updated_at"])
        prohibited = create_version(self.variant(copy="Details from the secret launch."))
        disconnected = create_version(self.variant(connection=False))
        too_long = create_version(self.variant(copy="x" * 281, network=SocialNetwork.X))

        self.assertTrue(prohibited.quality_check["hard_blocked"])
        self.assertTrue(disconnected.quality_check["hard_blocked"])
        self.assertTrue(too_long.quality_check["hard_blocked"])
        blocked_keys = {
            item["key"]
            for report in (prohibited.quality_check, disconnected.quality_check, too_long.quality_check)
            for item in report["deterministic"]
            if item["status"] == "BLOCKED"
        }
        self.assertEqual(blocked_keys, {"prohibited_topics", "authorization", "platform_fit"})

    def test_heuristics_and_language_judgments_are_review_only(self):
        recent = self.variant(copy="We are thrilled to announce! BUY NOW! LIMITED TIME! Contact team@example.com")
        recent.status = SocialPostState.PUBLISHED
        recent.save(update_fields=["status"])
        candidate = self.variant(copy=recent.copy)
        version = create_version(candidate, quality_analyzer=AdvisoryAnalyzer())

        self.assertFalse(version.quality_check["hard_blocked"])
        review_keys = {
            item["key"]
            for section in ("deterministic", "suggestions")
            for item in version.quality_check[section]
            if item["status"] == "REVIEW_SUGGESTED"
        }
        self.assertTrue({"recent_similarity", "hook_quality", "promotional_intensity", "sensitive_data"}.issubset(review_keys))
        self.assertTrue({"voice_match", "unsupported_claims", "copyright_attribution"}.issubset(review_keys))

    def test_recent_post_similarity_is_workspace_isolated(self):
        other_workspace = Workspace.objects.create(name="Other quality workspace")
        other_post = SocialPost.objects.create(workspace=other_workspace, idea_title="Private")
        SocialPostVariant.objects.create(
            post=other_post,
            network=SocialNetwork.LINKEDIN,
            copy="A uniquely matching private post",
            scheduled_for=timezone.now(),
            status=SocialPostState.PUBLISHED,
        )
        version = create_version(self.variant(copy="A uniquely matching private post"))
        similarity = next(item for item in version.quality_check["deterministic"] if item["key"] == "recent_similarity")
        self.assertEqual(similarity["status"], "PASS")

    def test_copy_change_creates_a_new_version_and_reruns_checks_without_rewriting(self):
        variant = self.variant(copy="First exact copy")
        first = create_version(variant)
        edit_variant(variant, copy="Second exact copy with team@example.com")
        second = variant.versions.latest("version")

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(second.copy, "Second exact copy with team@example.com")
        sensitive = next(item for item in second.quality_check["deterministic"] if item["key"] == "sensitive_data")
        self.assertEqual(sensitive["status"], "REVIEW_SUGGESTED")

    def test_advisory_findings_do_not_prevent_approval(self):
        variant = self.variant()
        version = create_version(variant, quality_analyzer=AdvisoryAnalyzer())
        with patch("integrations.social.services.studio.publishing_provider_registry.create", return_value=FakeUploadPostProvider()):
            approved = approve_exact_version(variant, version.id, user=self.user)
        self.assertEqual(approved.approved_version_id, version.id)

    def test_hard_blocked_version_cannot_be_approved(self):
        variant = self.variant(copy="x" * 281, network=SocialNetwork.X)
        version = create_version(variant)
        with self.assertRaises(ValidationError) as raised:
            approve_exact_version(variant, version.id, user=self.user)
        self.assertIn("quality", raised.exception.message_dict)
