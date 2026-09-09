from datetime import time

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from integrations.linkedin.models import ContentBrief, LinkedInAutomationSettings, LinkedInPost
from prospecting.models import Workspace

from .models import (
    BrandProfile,
    ContentSource,
    MediaAsset,
    PublishJob,
    PublishJobState,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostVersion,
    SocialProvider,
    SocialWorkspaceSettings,
)
from .services.linkedin_compat import record_provider_event, sync_post
from .services.scope import SocialContentScope


class SocialDomainModelTests(TestCase):
    def test_supported_network_and_provider_enums_are_explicit(self):
        self.assertEqual(set(SocialNetwork.values), {"LINKEDIN", "X", "INSTAGRAM"})
        self.assertIn("UPLOAD_POST", SocialProvider.values)
        self.assertIn("ZERNIO", SocialProvider.values)

    def test_linkedin_compatibility_creates_neutral_domain_and_versions(self):
        workspace = Workspace.objects.create(name="Social compatibility")
        legacy_settings = LinkedInAutomationSettings.objects.create(
            workspace=workspace,
            page_name="Example Brand",
            audience="Operations teams",
            content_pillars=["Operations"],
            publisher=LinkedInAutomationSettings.MANUAL,
        )
        legacy_source = ContentBrief.objects.create(
            settings=legacy_settings,
            label="Source",
            context="Reusable source text",
        )
        legacy_post = LinkedInPost.objects.create(
            settings=legacy_settings,
            brief=legacy_source,
            topic="Shared campaign idea",
            hook="A platform-neutral hook",
            body="LinkedIn version one",
            hashtags=["#Operations"],
            scheduled_for=timezone.now(),
        )

        social_post = sync_post(legacy_post)

        social_settings = SocialWorkspaceSettings.objects.get(workspace=workspace)
        self.assertEqual(social_settings.brand_name, "Example Brand")
        self.assertEqual(social_settings.brand_profile.audience, "Operations teams")
        self.assertEqual(ContentSource.objects.get(workspace=workspace).text_content, "Reusable source text")
        variant = social_post.variants.get(network=SocialNetwork.LINKEDIN)
        self.assertEqual(variant.copy, "LinkedIn version one")
        self.assertEqual(variant.versions.count(), 1)

        legacy_post.body = "LinkedIn version two"
        legacy_post.status = LinkedInPost.SCHEDULED
        legacy_post.approved_at = timezone.now()
        legacy_post.save()
        sync_post(legacy_post)

        variant.refresh_from_db()
        self.assertEqual(variant.copy, "LinkedIn version two")
        self.assertEqual(variant.versions.count(), 2)
        job = PublishJob.objects.get(variant=variant)
        self.assertEqual(job.approved_version.version, 2)
        self.assertEqual(job.status, PublishJobState.SCHEDULED)

    def test_social_post_versions_are_immutable(self):
        workspace = Workspace.objects.create(name="Immutable versions")
        legacy_settings = LinkedInAutomationSettings.objects.create(workspace=workspace)
        legacy_post = LinkedInPost.objects.create(
            settings=legacy_settings,
            topic="Versioned post",
            body="Approved snapshot",
            scheduled_for=timezone.now(),
        )
        social_post = sync_post(legacy_post)
        version = SocialPostVersion.objects.get(variant__post=social_post)

        version.copy = "Mutated snapshot"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            version.save()

    def test_cancelled_unapproved_draft_does_not_create_publish_job(self):
        workspace = Workspace.objects.create(name="Cancelled draft")
        legacy_settings = LinkedInAutomationSettings.objects.create(workspace=workspace)
        legacy_post = LinkedInPost.objects.create(
            settings=legacy_settings,
            topic="Never approved",
            body="Cancelled copy",
            status=LinkedInPost.CANCELLED,
            scheduled_for=timezone.now(),
        )

        social_post = sync_post(legacy_post)

        version = SocialPostVersion.objects.get(variant__post=social_post)
        self.assertIsNone(version.approved_at)
        self.assertFalse(PublishJob.objects.filter(variant__post=social_post).exists())

    def test_compatibility_sync_keeps_workspaces_isolated(self):
        workspace_a = Workspace.objects.create(name="Social workspace A")
        workspace_b = Workspace.objects.create(name="Social workspace B")
        settings_a = LinkedInAutomationSettings.objects.create(workspace=workspace_a, page_name="Brand A")
        settings_b = LinkedInAutomationSettings.objects.create(workspace=workspace_b, page_name="Brand B")
        post_a = LinkedInPost.objects.create(
            settings=settings_a,
            topic="A only",
            body="Workspace A content",
            image_url="https://example.com/a.png",
            status=LinkedInPost.SCHEDULED,
            approved_at=timezone.now(),
            scheduled_for=timezone.now(),
        )
        post_b = LinkedInPost.objects.create(
            settings=settings_b,
            topic="B only",
            body="Workspace B content",
            image_url="https://example.com/b.png",
            status=LinkedInPost.SCHEDULED,
            approved_at=timezone.now(),
            scheduled_for=timezone.now(),
        )

        sync_post(post_a)
        sync_post(post_b)
        record_provider_event(post_a, "test.poll", {"id": "event-a"})
        record_provider_event(post_b, "test.poll", {"id": "event-b"})

        scope = SocialContentScope(workspace_a)
        workspace_a_posts = scope.posts()
        workspace_a_connections = scope.connections()
        self.assertEqual(list(workspace_a_posts.values_list("idea_title", flat=True)), ["A only"])
        self.assertEqual(list(workspace_a_connections.values_list("display_name", flat=True)), ["Brand A"])
        self.assertFalse(workspace_a_posts.filter(legacy_linkedin_post_id=post_b.id).exists())
        self.assertEqual(scope.settings().count(), 1)
        self.assertEqual(scope.brand_profiles().count(), 1)
        self.assertEqual(scope.variants().count(), 1)
        self.assertEqual(scope.versions().count(), 1)
        self.assertEqual(scope.media_assets().count(), 1)
        self.assertEqual(scope.publish_jobs().count(), 1)
        self.assertEqual(scope.provider_events().count(), 1)


class LinkedInDataMigrationTests(TransactionTestCase):
    migrate_from = [
        ("linkedin_automation", "0003_generic_business_defaults"),
        ("social_content", "0001_initial"),
    ]
    migrate_to = [
        ("linkedin_automation", "0003_generic_business_defaults"),
        ("social_content", "0005_variant_media_storage"),
    ]

    def test_forward_and_reverse_migration_preserve_legacy_rows(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        WorkspaceModel = old_apps.get_model("prospecting", "Workspace")
        LegacySettings = old_apps.get_model("linkedin_automation", "LinkedInAutomationSettings")
        LegacyBrief = old_apps.get_model("linkedin_automation", "ContentBrief")
        LegacyPost = old_apps.get_model("linkedin_automation", "LinkedInPost")

        workspace = WorkspaceModel.objects.create(name="Migration workspace")
        legacy_settings = LegacySettings.objects.create(
            workspace_id=workspace.id,
            page_name="Migrated Brand",
            audience="Migrated audience",
            brand_voice="Migrated voice",
            content_pillars=["One", "Two"],
            calls_to_action=["Follow"],
            forbidden_topics=["Rumours"],
            image_style="Editorial",
            language="English",
            timezone="Asia/Kolkata",
            schedule_days=[0, 2, 4],
            post_time=time(10, 0),
            posts_per_week=3,
            queue_horizon_days=14,
            approval_mode="REQUIRE_APPROVAL",
            publisher="BUFFER",
            is_active=True,
        )
        legacy_brief = LegacyBrief.objects.create(
            settings_id=legacy_settings.id,
            label="Migrated source",
            context="Source body",
            is_evergreen=True,
            is_active=True,
        )
        legacy_post = LegacyPost.objects.create(
            settings_id=legacy_settings.id,
            brief_id=legacy_brief.id,
            topic="Migrated post",
            hook="Migrated hook",
            body="Migrated copy",
            hashtags=["#Migration"],
            image_url="https://example.com/image.png",
            alt_text="Migrated image",
            status="SCHEDULED",
            scheduled_for=timezone.now(),
            approved_at=timezone.now(),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        MigratedSettings = new_apps.get_model("social_content", "SocialWorkspaceSettings")
        MigratedBrand = new_apps.get_model("social_content", "BrandProfile")
        MigratedSource = new_apps.get_model("social_content", "ContentSource")
        MigratedPost = new_apps.get_model("social_content", "SocialPost")
        MigratedVariant = new_apps.get_model("social_content", "SocialPostVariant")
        MigratedVersion = new_apps.get_model("social_content", "SocialPostVersion")
        MigratedAsset = new_apps.get_model("social_content", "MediaAsset")
        MigratedJob = new_apps.get_model("social_content", "PublishJob")

        self.assertEqual(MigratedSettings.objects.get(workspace_id=workspace.id).brand_name, "Migrated Brand")
        self.assertEqual(MigratedBrand.objects.get(settings__workspace_id=workspace.id).voice, "Migrated voice")
        self.assertEqual(MigratedSource.objects.get(legacy_linkedin_brief_id=legacy_brief.id).text_content, "Source body")
        social_post = MigratedPost.objects.get(legacy_linkedin_post_id=legacy_post.id)
        variant = MigratedVariant.objects.get(post=social_post)
        self.assertEqual(variant.network, "LINKEDIN")
        self.assertEqual(variant.copy, "Migrated copy")
        version = MigratedVersion.objects.get(variant=variant)
        self.assertEqual(version.copy, "Migrated copy")
        self.assertEqual(version.scheduled_for, legacy_post.scheduled_for)
        self.assertEqual(variant.approved_version_id, version.id)
        migrated_asset = MigratedAsset.objects.get(variant=variant)
        self.assertEqual(migrated_asset.storage_url, "https://example.com/image.png")
        self.assertEqual(migrated_asset.source, "LEGACY")
        job = MigratedJob.objects.get(variant=variant)
        self.assertEqual(job.status, "SCHEDULED")
        self.assertEqual(job.connection_id, variant.connection_id)
        self.assertEqual(job.scheduled_for, legacy_post.scheduled_for)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        reversed_apps = executor.loader.project_state(self.migrate_from).apps
        self.assertFalse(reversed_apps.get_model("social_content", "SocialPost").objects.filter(legacy_linkedin_post_id=legacy_post.id).exists())
        self.assertTrue(reversed_apps.get_model("linkedin_automation", "LinkedInPost").objects.filter(pk=legacy_post.id).exists())

        MigrationExecutor(connection).migrate(self.migrate_to)
