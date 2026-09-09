from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.social.models import (
    AnalyticsSuggestion,
    AnalyticsSuggestionState,
    BrandProfile,
    ConnectionState,
    MediaAsset,
    PublishJob,
    PublishJobState,
    SocialConnection,
    SocialMetricName,
    SocialMetricObservation,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialPostVersion,
    SocialProvider,
    SocialWorkspaceSettings,
)
from integrations.social.publishing.types import (
    AccountMetricsResult,
    NormalizedMetricObservation,
    PostMetricsResult,
    ProviderName,
    PublishingMetricName,
)
from integrations.social.services.analytics import (
    ingest_account_metric_result,
    ingest_metric_result,
    refresh_published_metrics,
)
from prospecting.models import Workspace, WorkspaceMembership


@override_settings(CONTENT_AUTOMATION_DEV_BOOTSTRAP=False)
class SocialAnalyticsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="analytics-user")
        self.other_user = user_model.objects.create_user(username="other-analytics-user")
        self.workspace = Workspace.objects.create(name="Analytics workspace")
        self.other_workspace = Workspace.objects.create(name="Other analytics workspace")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.user, role=WorkspaceMembership.OWNER)
        WorkspaceMembership.objects.create(workspace=self.other_workspace, user=self.other_user, role=WorkspaceMembership.OWNER)
        settings = SocialWorkspaceSettings.objects.create(workspace=self.workspace, brand_name="Analytics Co")
        self.brand = BrandProfile.objects.create(settings=settings, content_pillars=["Growth", "Culture"])
        self.connections = {
            network: SocialConnection.objects.create(
                workspace=self.workspace,
                network=network,
                provider=SocialProvider.UPLOAD_POST,
                provider_profile_id=f"profile-{network.lower()}",
                provider_account_id=f"account-{network.lower()}",
                display_name=f"{network.title()} account",
                status=ConnectionState.CONNECTED,
            )
            for network in (SocialNetwork.LINKEDIN, SocialNetwork.X)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def published_job(self, *, index, network, pillar, image, interaction_rate):
        post = SocialPost.objects.create(
            workspace=self.workspace,
            idea_title=f"{pillar} topic {index}",
            metadata={"content_pillar": pillar},
            state=SocialPostState.PUBLISHED,
        )
        variant = SocialPostVariant.objects.create(
            post=post,
            connection=self.connections[network],
            network=network,
            copy=f"Published {pillar} post {index}",
            scheduled_for=timezone.now() - timedelta(days=index + 1),
            status=SocialPostState.PUBLISHED,
        )
        if image:
            MediaAsset.objects.create(
                workspace=self.workspace,
                variant=variant,
                asset_type="IMAGE",
                content_type="image/png",
                byte_size=100,
                width=100,
                height=100,
                alt_text="Chart",
            )
        version = SocialPostVersion.objects.create(
            variant=variant,
            version=1,
            copy=variant.copy,
            scheduled_for=variant.scheduled_for,
            approved_at=timezone.now() - timedelta(days=index + 2),
        )
        variant.approved_version = version
        variant.save(update_fields=["approved_version"])
        job = PublishJob.objects.create(
            variant=variant,
            connection=self.connections[network],
            approved_version=version,
            provider=SocialProvider.UPLOAD_POST,
            idempotency_key=f"analytics-job-{index}",
            external_id=f"external-{index}",
            status=PublishJobState.PUBLISHED,
            scheduled_for=variant.scheduled_for,
        )
        measured_at = timezone.now() - timedelta(hours=index)
        result = PostMetricsResult(
            provider=ProviderName.UPLOAD_POST,
            observations=(
                NormalizedMetricObservation(PublishingMetricName.IMPRESSIONS, 1000, measured_at, {"snapshot_id": f"snapshot-{index}"}),
                NormalizedMetricObservation(PublishingMetricName.LIKES, int(interaction_rate * 10), measured_at, {"snapshot_id": f"snapshot-{index}"}),
                NormalizedMetricObservation(PublishingMetricName.COMMENTS, 2, measured_at, {"snapshot_id": f"snapshot-{index}"}),
            ),
            unavailable_metrics=frozenset({PublishingMetricName.VIEWS, PublishingMetricName.CLICKS}),
            checked_at=measured_at,
        )
        ingest_metric_result(job=job, result=result)
        return job

    def seed_comparable_data(self):
        for index in range(4):
            self.published_job(
                index=index,
                network=SocialNetwork.LINKEDIN,
                pillar="Growth",
                image=True,
                interaction_rate=10,
            )
        for index in range(4, 8):
            self.published_job(
                index=index,
                network=SocialNetwork.X,
                pillar="Culture",
                image=False,
                interaction_rate=2,
            )

    def test_ingestion_is_idempotent_auditable_and_immutable(self):
        job = self.published_job(index=1, network=SocialNetwork.LINKEDIN, pillar="Growth", image=True, interaction_rate=5)
        existing = job.metric_observations.first()
        duplicate = PostMetricsResult(
            provider=ProviderName.UPLOAD_POST,
            observations=(NormalizedMetricObservation(
                PublishingMetricName(existing.metric_name),
                existing.value,
                existing.measured_at,
                existing.raw_reference,
            ),),
        )
        self.assertEqual(ingest_metric_result(job=job, result=duplicate), ())
        correction = PostMetricsResult(
            provider=ProviderName.UPLOAD_POST,
            observations=(NormalizedMetricObservation(
                PublishingMetricName(existing.metric_name),
                existing.value + 5,
                existing.measured_at,
                existing.raw_reference,
            ),),
        )
        self.assertEqual(len(ingest_metric_result(job=job, result=correction)), 1)
        self.assertTrue(existing.raw_reference["snapshot_id"])
        existing.value += 1
        with self.assertRaisesMessage(ValidationError, "immutable"):
            existing.save()

    @patch("integrations.social.services.analytics.publishing_provider_registry.create")
    def test_periodic_refresh_uses_the_provider_snapshotted_on_the_job(self, create_provider):
        job = self.published_job(
            index=2,
            network=SocialNetwork.LINKEDIN,
            pillar="Growth",
            image=True,
            interaction_rate=5,
        )
        measured_at = timezone.now()
        adapter = Mock()
        adapter.get_post_metrics.return_value = PostMetricsResult(
            provider=ProviderName.UPLOAD_POST,
            observations=(NormalizedMetricObservation(
                PublishingMetricName.CLICKS,
                17,
                measured_at,
                {"snapshot_id": "click-snapshot"},
            ),),
            checked_at=measured_at,
        )
        adapter.get_account_metrics.return_value = AccountMetricsResult(
            provider=ProviderName.UPLOAD_POST,
            unavailable_metrics=frozenset({PublishingMetricName.FOLLOWER_GROWTH}),
            checked_at=measured_at,
        )
        create_provider.return_value = adapter

        result = refresh_published_metrics()

        create_provider.assert_any_call(ProviderName.UPLOAD_POST)
        request = adapter.get_post_metrics.call_args.args[0]
        self.assertEqual(request.external_id, job.external_id)
        self.assertEqual(request.provider_account_id, job.connection.provider_account_id)
        self.assertEqual(
            result,
            {"checked": 1, "connections_checked": 2, "observations": 1, "failed": 0},
        )
        self.assertTrue(job.metric_observations.filter(metric_name=SocialMetricName.CLICKS, value=17).exists())

    def test_dashboard_compares_dimensions_and_marks_unavailable_metrics(self):
        self.seed_comparable_data()
        response = self.client.get(reverse("social-analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["label"] for row in response.data["comparisons"]["platform"]}, {"LinkedIn", "X"})
        self.assertEqual({row["label"] for row in response.data["comparisons"]["content_pillar"]}, {"Growth", "Culture"})
        self.assertEqual({row["label"] for row in response.data["comparisons"]["format"]}, {"Image", "Text"})
        self.assertFalse(response.data["summary"]["VIEWS"]["available"])
        self.assertIsNone(response.data["summary"]["VIEWS"]["value"])
        self.assertTrue(response.data["summary"]["IMPRESSIONS"]["available"])
        self.assertIn("do not prove", response.data["data_note"])

    def test_follower_growth_is_account_level_and_not_attributed_to_post_segments(self):
        job = self.published_job(
            index=3,
            network=SocialNetwork.LINKEDIN,
            pillar="Growth",
            image=True,
            interaction_rate=5,
        )
        measured_at = timezone.now()
        created = ingest_account_metric_result(
            connection=job.connection,
            result=AccountMetricsResult(
                provider=ProviderName.UPLOAD_POST,
                observations=(NormalizedMetricObservation(
                    PublishingMetricName.FOLLOWER_GROWTH,
                    14,
                    measured_at,
                    {"account_snapshot": "daily-1"},
                ),),
                checked_at=measured_at,
            ),
        )

        self.assertEqual(len(created), 1)
        self.assertIsNone(created[0].variant_id)
        dashboard = self.client.get(reverse("social-analytics"))
        self.assertTrue(dashboard.data["summary"]["FOLLOWER_GROWTH"]["available"])
        self.assertEqual(dashboard.data["summary"]["FOLLOWER_GROWTH"]["value"], 14)
        platform_row = dashboard.data["comparisons"]["platform"][0]
        self.assertFalse(platform_row["metrics"]["FOLLOWER_GROWTH"]["available"])

    def test_enough_data_creates_non_causal_suggestions_and_only_accept_applies_rule(self):
        self.seed_comparable_data()
        direct_update = self.client.put(
            reverse("social-brand-brain"),
            {"performance_rules": ["An unreviewed rule"]},
            format="json",
        )
        self.assertEqual(direct_update.status_code, 400)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.performance_rules, [])

        dashboard = self.client.get(reverse("social-analytics"))
        pending = [item for item in dashboard.data["suggestions"] if item["status"] == AnalyticsSuggestionState.PENDING]
        self.assertGreaterEqual(len(pending), 2)
        self.assertLessEqual(len(pending), 3)
        self.assertTrue(all("not evidence" in item["rationale"] for item in pending))

        dismissed = self.client.post(
            reverse("social-analytics-suggestion", args=[pending[0]["id"]]),
            {"action": "DISMISS"},
            format="json",
        )
        self.assertEqual(dismissed.status_code, 200)
        self.brand.refresh_from_db()
        self.assertNotIn(pending[0]["rule"], self.brand.performance_rules)

        accepted = self.client.post(
            reverse("social-analytics-suggestion", args=[pending[1]["id"]]),
            {"action": "ACCEPT"},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200)
        self.brand.refresh_from_db()
        self.assertIn(pending[1]["rule"], self.brand.performance_rules)
        self.assertGreaterEqual(self.brand.versions.count(), 1)

    def test_metrics_and_suggestion_actions_are_workspace_isolated(self):
        self.seed_comparable_data()
        self.client.get(reverse("social-analytics"))
        suggestion = AnalyticsSuggestion.objects.filter(workspace=self.workspace).first()
        self.client.force_authenticate(self.other_user)
        dashboard = self.client.get(reverse("social-analytics"))
        action = self.client.post(
            reverse("social-analytics-suggestion", args=[suggestion.id]),
            {"action": "ACCEPT"},
            format="json",
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertFalse(dashboard.data["summary"]["IMPRESSIONS"]["available"])
        self.assertEqual(action.status_code, 404)
        self.assertEqual(SocialMetricObservation.objects.filter(workspace=self.other_workspace).count(), 0)
