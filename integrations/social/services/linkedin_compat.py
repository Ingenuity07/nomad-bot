from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone

from integrations.social.models import (
    BrandProfile,
    ConnectionState,
    ContentSource,
    ContentSourceType,
    MediaAsset,
    MediaAssetSource,
    MediaAssetType,
    ProviderEvent,
    PublishJob,
    PublishJobState,
    SocialAccountType,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialPostVersion,
    SocialProvider,
    SocialWorkspaceSettings,
)


LEGACY_POST_STATES = {
    "DRAFT": SocialPostState.DRAFT,
    "SCHEDULED": SocialPostState.SCHEDULED,
    "READY": SocialPostState.APPROVED,
    "PUBLISHING": SocialPostState.PUBLISHING,
    "SUBMITTED": SocialPostState.SUBMITTED,
    "PUBLISHED": SocialPostState.PUBLISHED,
    "FAILED": SocialPostState.FAILED,
    "CANCELLED": SocialPostState.CANCELLED,
}
LEGACY_JOB_STATES = {
    "SCHEDULED": PublishJobState.SCHEDULED,
    "READY": PublishJobState.APPROVED,
    "PUBLISHING": PublishJobState.PUBLISHING,
    "SUBMITTED": PublishJobState.SUBMITTED,
    "PUBLISHED": PublishJobState.PUBLISHED,
    "FAILED": PublishJobState.FAILED,
    "CANCELLED": PublishJobState.CANCELLED,
}
LEGACY_APPROVED_STATES = {"SCHEDULED", "READY", "PUBLISHING", "SUBMITTED", "PUBLISHED", "FAILED"}


def _legacy_provider(value):
    return value if value in {
        SocialProvider.MANUAL,
        SocialProvider.BUFFER,
        SocialProvider.N8N,
        SocialProvider.WEBHOOK,
    } else SocialProvider.MANUAL


def _connection_identifiers(legacy_settings):
    provider = _legacy_provider(legacy_settings.publisher)
    if provider == SocialProvider.BUFFER:
        return "ORGANIZATION", django_settings.BUFFER_CHANNEL_ID
    if provider == SocialProvider.N8N:
        return django_settings.N8N_LINKEDIN_TARGET, ""
    return "ORGANIZATION", ""


@transaction.atomic
def sync_settings(legacy_settings):
    social_settings, _ = SocialWorkspaceSettings.objects.update_or_create(
        workspace_id=legacy_settings.workspace_id,
        defaults={
            "brand_name": legacy_settings.page_name,
            "language": legacy_settings.language,
            "timezone": legacy_settings.timezone,
            "approval_mode": legacy_settings.approval_mode,
            "schedule_days": legacy_settings.schedule_days,
            "post_time": legacy_settings.post_time,
            "posts_per_week": legacy_settings.posts_per_week,
            "queue_horizon_days": legacy_settings.queue_horizon_days,
            "is_active": legacy_settings.is_active,
            "legacy_linkedin_settings_id": legacy_settings.id,
        },
    )
    BrandProfile.objects.update_or_create(
        settings=social_settings,
        defaults={
            "audience": legacy_settings.audience,
            "voice": legacy_settings.brand_voice,
            "content_pillars": legacy_settings.content_pillars,
            "calls_to_action": legacy_settings.calls_to_action,
            "visual_direction": legacy_settings.image_style,
            "forbidden_topics": legacy_settings.forbidden_topics,
        },
    )
    provider = _legacy_provider(legacy_settings.publisher)
    provider_profile_id, provider_account_id = _connection_identifiers(legacy_settings)
    connection, _ = SocialConnection.objects.update_or_create(
        legacy_linkedin_settings_id=legacy_settings.id,
        defaults={
            "workspace_id": legacy_settings.workspace_id,
            "network": SocialNetwork.LINKEDIN,
            "provider": provider,
            "provider_profile_id": provider_profile_id,
            "provider_account_id": provider_account_id,
            "display_name": legacy_settings.page_name,
            "account_type": SocialAccountType.ORGANIZATION,
            "status": ConnectionState.DISCONNECTED if provider == SocialProvider.MANUAL else ConnectionState.CONNECTING,
            "capabilities": {
                "publish_text": True,
                "publish_image": True,
                "legacy_compatibility": True,
            },
        },
    )
    return social_settings, connection


@transaction.atomic
def sync_source(legacy_source):
    source, _ = ContentSource.objects.update_or_create(
        legacy_linkedin_brief_id=legacy_source.id,
        defaults={
            "workspace_id": legacy_source.settings.workspace_id,
            "source_type": ContentSourceType.TEXT,
            "label": legacy_source.label,
            "text_content": legacy_source.context,
            "metadata": {
                "is_evergreen": legacy_source.is_evergreen,
                "legacy_compatibility": True,
            },
            "is_reusable": legacy_source.is_evergreen,
            "is_active": legacy_source.is_active,
        },
    )
    return source


def _sync_media(legacy_post, variant):
    if not legacy_post.image_url and not legacy_post.image_data:
        MediaAsset.objects.filter(legacy_linkedin_post_id=legacy_post.id).delete()
        return []
    storage_url = legacy_post.image_url or f"/api/v3/linkedin/posts/{legacy_post.id}/image/"
    asset, _ = MediaAsset.objects.update_or_create(
        legacy_linkedin_post_id=legacy_post.id,
        defaults={
            "workspace_id": legacy_post.settings.workspace_id,
            "variant": variant,
            "post": variant.post,
            "asset_type": MediaAssetType.IMAGE,
            "source": MediaAssetSource.LEGACY,
            "storage_url": storage_url,
            "content_type": legacy_post.image_content_type,
            "byte_size": len(legacy_post.image_data or b""),
            "alt_text": legacy_post.alt_text,
            "metadata": {
                "content_type": legacy_post.image_content_type,
                "image_prompt": legacy_post.image_prompt,
                "generation": legacy_post.generation_metadata.get("image", {}),
                "legacy_compatibility": True,
            },
        },
    )
    return [{
        "asset_id": str(asset.id),
        "asset_type": asset.asset_type,
        "storage_url": asset.publish_url,
        "content_type": asset.content_type,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
        "alt_text": asset.alt_text,
    }]


def _latest_or_new_version(variant, legacy_post, media_snapshot):
    latest = variant.versions.order_by("-version").first()
    approved_at = legacy_post.approved_at
    if approved_at is None and legacy_post.status in LEGACY_APPROVED_STATES:
        approved_at = legacy_post.created_at
    if latest and (
        latest.copy == legacy_post.body
        and latest.hashtags == legacy_post.hashtags
        and latest.media_snapshot == media_snapshot
        and latest.scheduled_for == legacy_post.scheduled_for
        and latest.approved_at == approved_at
    ):
        return latest
    from integrations.social.services.quality import build_quality_report

    quality_check = build_quality_report(
        variant=variant,
        copy=legacy_post.body,
        hashtags=list(legacy_post.hashtags),
        metadata=dict(variant.metadata),
        media_snapshot=media_snapshot,
    )
    return SocialPostVersion.objects.create(
        variant=variant,
        version=(latest.version + 1) if latest else 1,
        copy=legacy_post.body,
        hashtags=legacy_post.hashtags,
        metadata=dict(variant.metadata),
        media_snapshot=media_snapshot,
        quality_check=quality_check,
        scheduled_for=legacy_post.scheduled_for,
        approved_at=approved_at,
    )


@transaction.atomic
def sync_post(legacy_post):
    _, connection = sync_settings(legacy_post.settings)
    source = sync_source(legacy_post.brief) if legacy_post.brief_id else None
    state = LEGACY_POST_STATES.get(legacy_post.status, SocialPostState.DRAFT)
    social_post, _ = SocialPost.objects.update_or_create(
        legacy_linkedin_post_id=legacy_post.id,
        defaults={
            "workspace_id": legacy_post.settings.workspace_id,
            "source": source,
            "idea_title": legacy_post.topic,
            "idea_text": legacy_post.hook,
            "state": state,
            "metadata": {
                "generation": legacy_post.generation_metadata,
                "legacy_compatibility": True,
            },
        },
    )
    variant, _ = SocialPostVariant.objects.update_or_create(
        post=social_post,
        network=SocialNetwork.LINKEDIN,
        defaults={
            "connection": connection,
            "copy": legacy_post.body,
            "hashtags": legacy_post.hashtags,
            "scheduled_for": legacy_post.scheduled_for,
            "status": state,
            "failure_reason": legacy_post.failure_reason,
        },
    )
    media_snapshot = _sync_media(legacy_post, variant)
    version = _latest_or_new_version(variant, legacy_post, media_snapshot)
    approved_version = version if version.approved_at else None
    if variant.approved_version_id != (approved_version.id if approved_version else None):
        variant.approved_version = approved_version
        variant.save(update_fields=["approved_version", "updated_at"])
    job_state = LEGACY_JOB_STATES.get(legacy_post.status)
    if legacy_post.status == "CANCELLED" and legacy_post.approved_at is None and not legacy_post.external_post_id:
        job_state = None
    if job_state:
        job, _ = PublishJob.objects.get_or_create(
            idempotency_key=str(legacy_post.id),
            defaults={
                "variant": variant,
                "connection": connection,
                "approved_version": version,
                "provider": _legacy_provider(legacy_post.settings.publisher),
                "scheduled_for": version.scheduled_for,
            },
        )
        job.variant = variant
        job.connection = connection
        job.approved_version = version
        # Provider is an immutable routing snapshot. A later settings change
        # must not move an already scheduled job to another provider.
        job.external_id = legacy_post.external_post_id
        job.status = job_state
        job.scheduled_for = version.scheduled_for
        job.failure_message = legacy_post.failure_reason
        job.diagnostic_details = {}
        if legacy_post.status in {"PUBLISHING", "SUBMITTED", "PUBLISHED", "FAILED"}:
            job.attempt_count = max(job.attempt_count, 1)
        if legacy_post.status in {"SUBMITTED", "PUBLISHED"} and job.submitted_at is None:
            job.submitted_at = timezone.now()
        if legacy_post.status in {"PUBLISHED", "FAILED", "CANCELLED"}:
            job.completed_at = legacy_post.published_at or timezone.now()
        job.save()
    return social_post


@transaction.atomic
def record_provider_event(legacy_post, event_type, payload, *, external_event_id=""):
    social_post = sync_post(legacy_post)
    variant = social_post.variants.get(network=SocialNetwork.LINKEDIN)
    job = variant.publish_jobs.filter(idempotency_key=str(legacy_post.id)).first()
    normalized_status = job.status if job else None
    return ProviderEvent.objects.create(
        workspace_id=legacy_post.settings.workspace_id,
        connection=variant.connection,
        publish_job=job,
        provider=_legacy_provider(legacy_post.settings.publisher),
        external_event_id=str(
            external_event_id or payload.get("event_id") or payload.get("id") or ""
        ),
        event_type=event_type,
        normalized_status=normalized_status,
        payload=payload,
        occurred_at=timezone.now(),
    )
