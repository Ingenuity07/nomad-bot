from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from integrations.linkedin.models import LinkedInAutomationSettings
from integrations.social.models import (
    AnalyticsSuggestion,
    ContentSource,
    ContentStudioOnboarding,
    ProviderEvent,
    PublishJob,
    PublishJobState,
    SocialAuditEvent,
    SocialAuditEventType,
    SocialConnection,
    SocialMetricObservation,
    SocialPost,
    SocialWorkspaceSettings,
    StoryInterview,
)
from integrations.social.services.audit import record_audit_event
from prospecting.models import WorkspaceMembership


ACTIVE_DELETE_BLOCKERS = {
    PublishJobState.PUBLISHING,
    PublishJobState.SUBMITTED,
    PublishJobState.UNKNOWN,
}


def _membership_for(user, workspace):
    if getattr(user, "is_superuser", False):
        return None
    membership = WorkspaceMembership.objects.filter(
        user=user,
        workspace=workspace,
    ).first()
    if membership is None:
        raise PermissionDenied("You do not have access to this workspace.")
    return membership


def require_workspace_admin(user, workspace):
    membership = _membership_for(user, workspace)
    if membership and membership.role not in {
        WorkspaceMembership.OWNER,
        WorkspaceMembership.ADMIN,
    }:
        raise PermissionDenied("A workspace administrator must perform this action.")


def require_workspace_owner(user, workspace):
    membership = _membership_for(user, workspace)
    if membership and membership.role != WorkspaceMembership.OWNER:
        raise PermissionDenied("Only the workspace owner can delete Content Studio data.")


def _iso(value):
    return value.isoformat() if value else None


def export_workspace_content(*, workspace, actor):
    require_workspace_admin(actor, workspace)
    settings_row = SocialWorkspaceSettings.objects.filter(workspace=workspace).first()
    posts = SocialPost.objects.filter(workspace=workspace).prefetch_related(
        "source_references__source",
        "variants__media_assets",
        "variants__versions",
    )
    payload = {
        "format": "content-studio-export-v1",
        "workspace": {"id": str(workspace.id), "name": workspace.name},
        "settings": None if settings_row is None else {
            "brand_name": settings_row.brand_name,
            "language": settings_row.language,
            "timezone": settings_row.timezone,
            "approval_mode": settings_row.approval_mode,
            "schedule_days": settings_row.schedule_days,
            "post_time": settings_row.post_time.isoformat(),
            "posts_per_week": settings_row.posts_per_week,
        },
        "sources": [
            {
                "id": str(source.id),
                "type": source.source_type,
                "label": source.label,
                "text": source.extracted_text or source.text_content,
                "source_url": source.source_url,
                "original_filename": source.original_filename,
                "processing_status": source.processing_status,
                "created_at": _iso(source.created_at),
            }
            for source in ContentSource.objects.filter(workspace=workspace)
        ],
        "connections": [
            {
                "id": str(connection.id),
                "network": connection.network,
                "display_name": connection.display_name,
                "account_type": connection.account_type,
                "status": connection.status,
                "connected_at": _iso(connection.connected_at),
                "disconnected_at": _iso(connection.disconnected_at),
            }
            for connection in SocialConnection.objects.filter(workspace=workspace)
        ],
        "posts": [],
        "audit_events": [
            {
                "event_type": event.event_type,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "details": {
                    key: value
                    for key, value in event.details.items()
                    if "provider" not in key.lower()
                },
                "created_at": _iso(event.created_at),
            }
            for event in SocialAuditEvent.objects.filter(workspace=workspace)
        ],
    }
    for post in posts:
        post_payload = {
            "id": str(post.id),
            "idea_title": post.idea_title,
            "idea_text": post.idea_text,
            "state": post.state,
            "created_at": _iso(post.created_at),
            "updated_at": _iso(post.updated_at),
            "source_ids": [str(ref.source_id) for ref in post.source_references.all()],
            "variants": [],
        }
        for variant in post.variants.all():
            post_payload["variants"].append({
                "id": str(variant.id),
                "network": variant.network,
                "copy": variant.copy,
                "hashtags": variant.hashtags,
                "status": variant.status,
                "scheduled_for": _iso(variant.scheduled_for),
                "media": [
                    {
                        "id": str(asset.id),
                        "type": asset.asset_type,
                        "filename": asset.original_filename,
                        "content_type": asset.content_type,
                        "byte_size": asset.byte_size,
                        "width": asset.width,
                        "height": asset.height,
                        "duration_ms": asset.duration_ms,
                        "alt_text": asset.alt_text,
                        "sort_order": asset.sort_order,
                    }
                    for asset in variant.media_assets.all()
                ],
                "versions": [
                    {
                        "id": str(version.id),
                        "version": version.version,
                        "copy": version.copy,
                        "hashtags": version.hashtags,
                        "scheduled_for": _iso(version.scheduled_for),
                        "approved_at": _iso(version.approved_at),
                        "quality_check": version.quality_check,
                    }
                    for version in variant.versions.all()
                ],
            })
        payload["posts"].append(post_payload)
    record_audit_event(
        workspace=workspace,
        event_type=SocialAuditEventType.DATA_EXPORTED,
        actor=actor,
        details={"format": payload["format"]},
    )
    return payload


@transaction.atomic
def delete_workspace_content(*, workspace, actor):
    require_workspace_owner(actor, workspace)
    if PublishJob.objects.filter(
        variant__post__workspace=workspace,
        status__in=ACTIVE_DELETE_BLOCKERS,
    ).exists():
        raise ValidationError(
            "Wait for posts currently being published to finish before deleting Content Studio data."
        )
    counts = {
        "posts": SocialPost.objects.filter(workspace=workspace).count(),
        "sources": ContentSource.objects.filter(workspace=workspace).count(),
        "connections": SocialConnection.objects.filter(workspace=workspace).count(),
        "metrics": SocialMetricObservation.objects.filter(workspace=workspace).count(),
    }
    # Delete the generic graph before connections and settings with protected children.
    ProviderEvent.objects.filter(workspace=workspace).delete()
    SocialMetricObservation.objects.filter(workspace=workspace).delete()
    AnalyticsSuggestion.objects.filter(workspace=workspace).delete()
    StoryInterview.objects.filter(workspace=workspace).delete()
    PublishJob.objects.filter(variant__post__workspace=workspace).delete()
    SocialPost.objects.filter(workspace=workspace).delete()
    ContentSource.objects.filter(workspace=workspace).delete()
    SocialConnection.objects.filter(workspace=workspace).delete()
    ContentStudioOnboarding.objects.filter(workspace=workspace).delete()
    SocialWorkspaceSettings.objects.filter(workspace=workspace).delete()
    LinkedInAutomationSettings.objects.filter(workspace=workspace).delete()
    SocialAuditEvent.objects.filter(workspace=workspace).delete()
    record_audit_event(
        workspace=workspace,
        event_type=SocialAuditEventType.DATA_DELETED,
        actor=actor,
        details=counts,
    )
    return counts
