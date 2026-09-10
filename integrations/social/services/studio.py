import hashlib
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from integrations.social.media import validate_variant_media
from integrations.social.models import (
    ConnectionState,
    ContentStudioOnboarding,
    ContentSource,
    ContentSourceProcessingState,
    PublishJobState,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialPostVersion,
    SocialWorkspaceSettings,
)
from integrations.social.publishing.registry import publishing_provider_registry
from integrations.social.publishing.types import ConnectionUrlRequest, ProviderName, PublishingNetwork
from integrations.social.services.composer import NETWORK_LABELS, variant_validation
from integrations.social.services.lifecycle import cancel_variant, edit_variant


ACTIVE_APPROVED_STATES = {
    SocialPostState.APPROVED,
    SocialPostState.SCHEDULED,
    SocialPostState.PUBLISHING,
    SocialPostState.SUBMITTED,
    SocialPostState.PUBLISHED,
}
LIBRARY_FILTERS = {
    "DRAFT": {SocialPostState.DRAFT, SocialPostState.NEEDS_REVIEW},
    "SCHEDULED": {SocialPostState.APPROVED, SocialPostState.SCHEDULED, SocialPostState.PUBLISHING, SocialPostState.SUBMITTED},
    "PUBLISHED": {SocialPostState.PUBLISHED},
    "FAILED": {SocialPostState.FAILED, SocialPostState.CONNECTION_REQUIRED},
    "CANCELLED": {SocialPostState.CANCELLED},
}


def _review_group(variant):
    state = variant.metadata.get("review_state")
    if state == "CHANGES_REQUESTED":
        return "CHANGES_REQUESTED"
    if variant.status in ACTIVE_APPROVED_STATES:
        return "APPROVED"
    if variant.status == SocialPostState.DRAFT and not variant.post.legacy_linkedin_post_id:
        return ""
    if variant.status in {SocialPostState.DRAFT, SocialPostState.NEEDS_REVIEW}:
        return "NEEDS_REVIEW"
    return ""


def _media_snapshot_matches(variant, version):
    current = [
        (str(asset.id), asset.checksum_sha256, asset.alt_text, asset.sort_order)
        for asset in variant.media_assets.order_by("sort_order", "created_at")
    ]
    saved = [
        (str(item.get("asset_id") or ""), str(item.get("checksum_sha256") or ""), str(item.get("alt_text") or ""), index)
        for index, item in enumerate(version.media_snapshot)
    ]
    return current == saved


def _content_metadata(value):
    value = dict(value or {})
    value.pop("review_state", None)
    value.pop("review_note", None)
    value.pop("reviewed_at", None)
    return value


def version_matches_variant(variant, version):
    return (
        version.copy == variant.copy
        and list(version.hashtags) == list(variant.hashtags)
        and version.scheduled_for == variant.scheduled_for
        and _content_metadata(version.metadata) == _content_metadata(variant.metadata)
        and _media_snapshot_matches(variant, version)
    )


def serialize_version(version):
    metadata = dict(version.metadata)
    references = metadata.get("source_references")
    if isinstance(references, list):
        source_ids = [item.get("id") for item in references if isinstance(item, dict) and item.get("id")]
        available_ids = {
            str(value) for value in ContentSource.objects.filter(
                workspace=version.variant.post.workspace,
                pk__in=source_ids,
                is_active=True,
                processing_status=ContentSourceProcessingState.READY,
            ).exclude(extracted_text="").values_list("id", flat=True)
        }
        metadata["source_references"] = [
            {**item, "available": str(item.get("id")) in available_ids}
            for item in references if isinstance(item, dict)
        ]
    return {
        "id": str(version.id),
        "version": version.version,
        "copy": version.copy,
        "hashtags": version.hashtags,
        "metadata": metadata,
        "media": version.media_snapshot,
        "quality_check": version.quality_check,
        "scheduled_for": version.scheduled_for.isoformat(),
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
        "created_at": version.created_at.isoformat(),
    }


def serialize_variant_card(variant, *, include_versions=False):
    first_asset = next(iter(variant.media_assets.all()), None)
    data = {
        "id": str(variant.id),
        "post_id": str(variant.post_id),
        "topic": variant.post.idea_title,
        "source": variant.post.source.label if variant.post.source else "",
        "network": variant.network,
        "network_label": NETWORK_LABELS[variant.network],
        "account": {
            "id": str(variant.connection_id),
            "display_name": variant.connection.display_name,
            "account_type": variant.connection.get_account_type_display(),
            "health": "HEALTHY" if variant.connection.status == ConnectionState.CONNECTED else "NEEDS_ATTENTION",
        } if variant.connection else None,
        "copy": variant.copy,
        "hashtags": variant.hashtags,
        "metadata": variant.metadata,
        "scheduled_for": variant.scheduled_for.isoformat(),
        "status": variant.status,
        "review_group": _review_group(variant),
        "review_note": str(variant.metadata.get("review_note") or ""),
        "validation": variant_validation(variant),
        "media_thumbnail": first_asset.publish_url if first_asset and first_asset.asset_type == "IMAGE" else "",
        "media_count": len(variant.media_assets.all()),
        "updated_at": variant.updated_at.isoformat(),
    }
    if include_versions:
        data["versions"] = [serialize_version(version) for version in variant.versions.order_by("-version")]
        data["approved_version_id"] = str(variant.approved_version_id or "")
    return data


def review_queue(workspace):
    variants = (
        SocialPostVariant.objects.filter(post__workspace=workspace)
        .exclude(status__in=[SocialPostState.CANCELLED, SocialPostState.PUBLISHED])
        .select_related("post__source", "connection", "approved_version")
        .prefetch_related("media_assets", "versions")
        .order_by("-updated_at")
    )
    groups = {"NEEDS_REVIEW": [], "CHANGES_REQUESTED": [], "APPROVED": []}
    for variant in variants:
        group = _review_group(variant)
        if group:
            groups[group].append(serialize_variant_card(variant, include_versions=True))
    return groups


def _recalculate_post_state(post):
    statuses = list(post.variants.values_list("status", flat=True))
    if not statuses:
        return
    if all(state == SocialPostState.CANCELLED for state in statuses):
        state = SocialPostState.CANCELLED
    elif all(state in ACTIVE_APPROVED_STATES for state in statuses):
        state = SocialPostState.APPROVED
    elif any(state == SocialPostState.NEEDS_REVIEW for state in statuses):
        state = SocialPostState.NEEDS_REVIEW
    else:
        state = statuses[0]
    SocialPost.objects.filter(pk=post.pk).update(state=state, updated_at=timezone.now())


@transaction.atomic
def approve_exact_version(variant, version_id, *, user):
    # Lock only the variant row. Joining the nullable connection relation here
    # makes PostgreSQL reject FOR UPDATE on the nullable side of an outer join.
    variant = SocialPostVariant.objects.select_for_update().get(pk=variant.pk)
    version = SocialPostVersion.objects.filter(variant=variant, pk=version_id).order_by("-version").first()
    latest = variant.versions.order_by("-version").first()
    if version is None:
        raise ValidationError({"version_id": "Choose a version from this post."})
    if latest is None or latest.pk != version.pk or not version_matches_variant(variant, version):
        raise ValidationError({"version_id": "This version is no longer current. Review the latest version before approving."})
    if variant.connection is None or variant.connection.status != ConnectionState.CONNECTED:
        raise ValidationError({"connection": "Reconnect this social account before approving."})
    from integrations.social.services.quality import hard_block_messages

    blockers = hard_block_messages(version.quality_check)
    if blockers:
        raise ValidationError({"quality": blockers})
    adapter = publishing_provider_registry.create(ProviderName(variant.connection.provider))
    validate_variant_media(variant, adapter.capabilities)
    now = timezone.now()
    approved_by = user if getattr(user, "is_authenticated", False) else None
    SocialPostVersion.objects.filter(pk=version.pk).update(approved_at=now, approved_by=approved_by)
    metadata = dict(variant.metadata)
    metadata.update({"review_state": "APPROVED", "reviewed_at": now.isoformat()})
    metadata.pop("review_note", None)
    variant.approved_version = version
    variant.status = SocialPostState.APPROVED
    variant.failure_reason = ""
    variant.metadata = metadata
    variant.save(update_fields=["approved_version", "status", "failure_reason", "metadata", "updated_at"])
    _recalculate_post_state(variant.post)
    version.approved_at = now
    version.approved_by = approved_by
    return variant


@transaction.atomic
def request_changes(variant, *, note):
    note = str(note or "").strip()
    if not note:
        raise ValidationError({"note": "Explain what should change."})
    variant = SocialPostVariant.objects.select_for_update().select_related("post").get(pk=variant.pk)
    if variant.status in {SocialPostState.PUBLISHED, SocialPostState.PUBLISHING, SocialPostState.SUBMITTED, SocialPostState.CANCELLED}:
        raise ValidationError({"detail": "This post can no longer be returned for changes."})
    metadata = dict(variant.metadata)
    metadata.update({"review_state": "CHANGES_REQUESTED", "review_note": note[:1000], "reviewed_at": timezone.now().isoformat()})
    variant.metadata = metadata
    variant.approved_version = None
    variant.status = SocialPostState.NEEDS_REVIEW
    variant.save(update_fields=["metadata", "approved_version", "status", "updated_at"])
    _recalculate_post_state(variant.post)
    return variant


@transaction.atomic
def reject_variant(variant, *, note=""):
    variant = SocialPostVariant.objects.select_for_update().select_related("post").get(pk=variant.pk)
    metadata = dict(variant.metadata)
    metadata.update({"review_state": "REJECTED", "review_note": str(note or "").strip()[:1000], "reviewed_at": timezone.now().isoformat()})
    variant.metadata = metadata
    variant.save(update_fields=["metadata", "updated_at"])
    cancel_variant(variant)
    _recalculate_post_state(variant.post)
    return variant


@transaction.atomic
def batch_approve(workspace, approvals, *, user):
    if not isinstance(approvals, list) or not approvals:
        raise ValidationError({"approvals": "Select at least one current version."})
    approved = []
    for item in approvals:
        variant = SocialPostVariant.objects.filter(post__workspace=workspace, pk=item.get("variant_id")).first()
        if variant is None:
            raise ValidationError({"approvals": "One selected post is not available in this workspace."})
        approved.append(approve_exact_version(variant, item.get("version_id"), user=user))
    return approved


def calendar_range(anchor, view):
    local = anchor
    if view == "MONTH":
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:
        start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    return start, end


def calendar_items(workspace, *, start, end):
    variants = (
        SocialPostVariant.objects.filter(post__workspace=workspace, scheduled_for__gte=start, scheduled_for__lt=end)
        .select_related("post__source", "connection")
        .prefetch_related("media_assets")
        .order_by("scheduled_for")
    )
    return [serialize_variant_card(variant) for variant in variants]


@transaction.atomic
def reschedule_variant(variant, scheduled_for):
    now = timezone.now()
    if scheduled_for <= now:
        raise ValidationError({"scheduled_for": "Choose a future date and time."})
    if scheduled_for > now + timedelta(days=366):
        raise ValidationError({"scheduled_for": "Choose a date within the next year."})
    settings = SocialWorkspaceSettings.objects.filter(workspace=variant.post.workspace).first()
    if settings and settings.schedule_days:
        try:
            local_date = scheduled_for.astimezone(ZoneInfo(settings.timezone))
        except ZoneInfoNotFoundError:
            local_date = scheduled_for
        if local_date.weekday() not in settings.schedule_days:
            raise ValidationError({"scheduled_for": "Choose one of the posting days selected in Settings."})
    conflict_start = scheduled_for - timedelta(minutes=5)
    conflict_end = scheduled_for + timedelta(minutes=5)
    conflict = SocialPostVariant.objects.filter(
        post__workspace=variant.post.workspace,
        connection=variant.connection,
        scheduled_for__gt=conflict_start,
        scheduled_for__lt=conflict_end,
    ).exclude(pk=variant.pk).exclude(status=SocialPostState.CANCELLED).exists()
    if conflict:
        raise ValidationError({"scheduled_for": "Another post for this account is scheduled within five minutes. Choose another time."})
    return edit_variant(variant, scheduled_for=scheduled_for)


def library_posts(workspace, *, status="", query="", platform="", date_from=None, date_to=None, include_archived=False):
    posts = SocialPost.objects.filter(workspace=workspace).select_related("source").prefetch_related("variants__connection", "variants__media_assets")
    if not include_archived:
        posts = posts.exclude(metadata__has_key="archived_at")
    if status in LIBRARY_FILTERS:
        posts = posts.filter(state__in=LIBRARY_FILTERS[status])
    if query:
        posts = posts.filter(Q(idea_title__icontains=query) | Q(idea_text__icontains=query) | Q(source__label__icontains=query) | Q(variants__copy__icontains=query))
    if platform in SocialNetwork.values:
        posts = posts.filter(variants__network=platform)
    if date_from:
        posts = posts.filter(created_at__date__gte=date_from)
    if date_to:
        posts = posts.filter(created_at__date__lte=date_to)
    return posts.distinct().order_by("-updated_at")


@transaction.atomic
def duplicate_post(post, *, reuse_idea=False):
    clone = SocialPost.objects.create(
        workspace=post.workspace,
        source=post.source,
        idea_title=f"{post.idea_title} — copy"[:255] if not reuse_idea else post.idea_title,
        idea_text=post.idea_text,
        state=SocialPostState.DRAFT,
        metadata={"generation_controls": post.metadata.get("generation_controls", {})},
    )
    for variant in post.variants.select_related("connection"):
        SocialPostVariant.objects.create(
            post=clone,
            connection=variant.connection if variant.connection and variant.connection.status == ConnectionState.CONNECTED else None,
            network=variant.network,
            copy="" if reuse_idea else variant.copy,
            hashtags=[] if reuse_idea else list(variant.hashtags),
            metadata={} if reuse_idea else _content_metadata(variant.metadata),
            scheduled_for=timezone.now() + timedelta(days=1),
            status=SocialPostState.DRAFT,
        )
    return clone


def archive_post(post):
    if post.variants.filter(status__in=[SocialPostState.PUBLISHING, SocialPostState.SUBMITTED]).exists():
        raise ValidationError({"detail": "A post being published cannot be archived yet."})
    metadata = dict(post.metadata)
    metadata["archived_at"] = timezone.now().isoformat()
    post.metadata = metadata
    post.save(update_fields=["metadata", "updated_at"])
    return post


def serialize_connection(connection):
    messages = {
        ConnectionState.CONNECTED: "Ready",
        ConnectionState.CONNECTING: "Connection in progress",
        ConnectionState.ERROR: "Reconnect to restore publishing",
        ConnectionState.REVOKED: "Access expired — reconnect",
        ConnectionState.DISCONNECTED: "Disconnected",
    }
    return {
        "id": str(connection.id),
        "network": connection.network,
        "network_label": NETWORK_LABELS[connection.network],
        "display_name": connection.display_name,
        "account_type": connection.get_account_type_display(),
        "status": connection.status,
        "health": "HEALTHY" if connection.status == ConnectionState.CONNECTED else "NEEDS_ATTENTION",
        "message": messages[connection.status],
        "connected_at": connection.connected_at.isoformat() if connection.connected_at else None,
        "last_checked_at": connection.updated_at.isoformat(),
    }


@transaction.atomic
def disconnect_connection(connection):
    connection = SocialConnection.objects.select_for_update().get(pk=connection.pk)
    if connection.publish_jobs.filter(
        status__in={PublishJobState.PUBLISHING, PublishJobState.SUBMITTED, PublishJobState.UNKNOWN},
    ).exists():
        raise ValidationError({
            "detail": "A post is still being processed for this account. Wait for it to finish before disconnecting."
        })
    affected_variants = list(
        SocialPostVariant.objects.select_for_update().select_related("post").filter(
            connection=connection,
            publish_jobs__status=PublishJobState.SCHEDULED,
        ).distinct()
    )
    connection.publish_jobs.filter(status=PublishJobState.SCHEDULED).update(
        status=PublishJobState.CONNECTION_REQUIRED,
        next_attempt_at=None,
        failure_message="Reconnect the social account before publishing.",
        claim_token=None,
        claimed_at=None,
    )
    for variant in affected_variants:
        variant.status = SocialPostState.CONNECTION_REQUIRED
        variant.failure_reason = "Reconnect the social account before publishing."
        variant.save(update_fields=["status", "failure_reason", "updated_at"])
        _recalculate_post_state(variant.post)
    connection.status = ConnectionState.DISCONNECTED
    connection.disconnected_at = timezone.now()
    connection.save(update_fields=["status", "disconnected_at", "updated_at"])
    return connection


@transaction.atomic
def reconnect_connection(connection):
    try:
        provider = ProviderName(connection.provider)
        network = PublishingNetwork(connection.network)
    except ValueError as exc:
        raise ValidationError({"detail": "This account cannot be reconnected from Content Studio."}) from exc
    state = secrets.token_urlsafe(32)
    result = publishing_provider_registry.create(provider).get_connection_url(ConnectionUrlRequest(
        workspace_id=connection.workspace_id,
        redirect_uri="",
        state=state,
        requested_networks=(network,),
    ))
    ContentStudioOnboarding.objects.get_or_create(workspace=connection.workspace)
    onboarding = ContentStudioOnboarding.objects.select_for_update().get(workspace=connection.workspace)
    onboarding.connection_provider = provider.value
    onboarding.connection_state = hashlib.sha256(state.encode("utf-8")).hexdigest()
    onboarding.connection_expires_at = result.expires_at or timezone.now() + timedelta(minutes=30)
    onboarding.connection_error = ""
    onboarding.save(update_fields=["connection_provider", "connection_state", "connection_expires_at", "connection_error", "updated_at"])
    connection.status = ConnectionState.CONNECTING
    connection.save(update_fields=["status", "updated_at"])
    return result


def home_summary(workspace):
    variants = SocialPostVariant.objects.filter(post__workspace=workspace).select_related("post__source", "connection").prefetch_related("media_assets")
    reviews = [item for item in variants if _review_group(item) in {"NEEDS_REVIEW", "CHANGES_REQUESTED"}]
    upcoming = [item for item in variants if item.status in {SocialPostState.APPROVED, SocialPostState.SCHEDULED, SocialPostState.SUBMITTED} and item.scheduled_for >= timezone.now()]
    failures = [item for item in variants if item.status in {SocialPostState.FAILED, SocialPostState.CONNECTION_REQUIRED}]
    return {
        "needs_approval": [serialize_variant_card(item) for item in sorted(reviews, key=lambda row: row.updated_at, reverse=True)[:5]],
        "upcoming": [serialize_variant_card(item) for item in sorted(upcoming, key=lambda row: row.scheduled_for)[:5]],
        "failures": [serialize_variant_card(item) for item in sorted(failures, key=lambda row: row.updated_at, reverse=True)[:5]],
        "connections_needing_attention": SocialConnection.objects.filter(workspace=workspace).exclude(status=ConnectionState.CONNECTED).count(),
    }
