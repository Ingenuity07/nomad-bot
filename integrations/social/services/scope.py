from dataclasses import dataclass

from prospecting.models import Workspace

from integrations.social.models import (
    BrandProfile,
    AnalyticsSuggestion,
    ContentSource,
    MediaAsset,
    ProviderEvent,
    PublishJob,
    SocialConnection,
    SocialPost,
    SocialPostVariant,
    SocialPostVersion,
    SocialWorkspaceSettings,
    SocialMetricObservation,
    SocialAuditEvent,
)


@dataclass(frozen=True)
class SocialContentScope:
    """Workspace-bound query entry point for future neutral-domain APIs."""

    workspace: Workspace

    def settings(self):
        return SocialWorkspaceSettings.objects.filter(workspace=self.workspace)

    def brand_profiles(self):
        return BrandProfile.objects.filter(settings__workspace=self.workspace)

    def sources(self):
        return ContentSource.objects.filter(workspace=self.workspace)

    def connections(self):
        return SocialConnection.objects.filter(workspace=self.workspace)

    def posts(self):
        return SocialPost.objects.filter(workspace=self.workspace)

    def variants(self):
        return SocialPostVariant.objects.filter(post__workspace=self.workspace)

    def versions(self):
        return SocialPostVersion.objects.filter(variant__post__workspace=self.workspace)

    def media_assets(self):
        return MediaAsset.objects.filter(workspace=self.workspace)

    def publish_jobs(self):
        return PublishJob.objects.filter(variant__post__workspace=self.workspace)

    def provider_events(self):
        return ProviderEvent.objects.filter(workspace=self.workspace)

    def metric_observations(self):
        return SocialMetricObservation.objects.filter(workspace=self.workspace)

    def analytics_suggestions(self):
        return AnalyticsSuggestion.objects.filter(workspace=self.workspace)

    def audit_events(self):
        return SocialAuditEvent.objects.filter(workspace=self.workspace)
