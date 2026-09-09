import uuid
from dataclasses import dataclass
from datetime import timedelta
from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from integrations.social.models import (
    ConnectionState,
    ProviderEvent,
    PublishAttempt,
    PublishJob,
    PublishJobState,
    SocialPostState,
    SocialPostVariant,
    SocialPostVersion,
    SocialConnection,
    SocialAuditEventType,
)
from integrations.social.services.audit import record_audit_event
from integrations.social.media import safe_publish_url, validate_variant_media, validate_version_media
from integrations.social.publishing.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderPermanentFailureError,
    ProviderRateLimitError,
    ProviderTemporaryFailureError,
    ProviderUnknownOutcomeError,
    ProviderValidationError,
    PublishingProviderError,
)
from integrations.social.publishing.registry import publishing_provider_registry
from integrations.social.publishing.types import (
    GetPublishStatusRequest,
    NormalizedPost,
    PostMedia,
    ProviderErrorCategory,
    ProviderName,
    PublishNowRequest,
    PublishOutcome,
    PublishingMediaType,
    PublishingNetwork,
    SocialAccount,
    WebhookRequest,
)


EDITABLE_STATES = {
    SocialPostState.DRAFT,
    SocialPostState.NEEDS_REVIEW,
    SocialPostState.APPROVED,
    SocialPostState.SCHEDULED,
    SocialPostState.FAILED,
    SocialPostState.CONNECTION_REQUIRED,
}
TERMINAL_STATES = {
    PublishJobState.PUBLISHED,
    PublishJobState.FAILED,
    PublishJobState.CANCELLED,
}
RECONCILE_STATES = {
    PublishJobState.SUBMITTED,
    PublishJobState.UNKNOWN,
}

VARIANT_TRANSITIONS = {
    SocialPostState.DRAFT: {SocialPostState.NEEDS_REVIEW, SocialPostState.APPROVED, SocialPostState.CANCELLED},
    SocialPostState.NEEDS_REVIEW: {SocialPostState.APPROVED, SocialPostState.CANCELLED},
    SocialPostState.APPROVED: {
        SocialPostState.SCHEDULED,
        SocialPostState.NEEDS_REVIEW,
        SocialPostState.CONNECTION_REQUIRED,
        SocialPostState.CANCELLED,
    },
    SocialPostState.SCHEDULED: {
        SocialPostState.PUBLISHING,
        SocialPostState.NEEDS_REVIEW,
        SocialPostState.CONNECTION_REQUIRED,
        SocialPostState.CANCELLED,
    },
    SocialPostState.PUBLISHING: {
        SocialPostState.SCHEDULED,
        SocialPostState.SUBMITTED,
        SocialPostState.PUBLISHED,
        SocialPostState.FAILED,
        SocialPostState.CONNECTION_REQUIRED,
    },
    SocialPostState.SUBMITTED: {
        SocialPostState.SCHEDULED,
        SocialPostState.PUBLISHED,
        SocialPostState.FAILED,
        SocialPostState.CONNECTION_REQUIRED,
    },
    SocialPostState.FAILED: {
        SocialPostState.NEEDS_REVIEW,
        SocialPostState.APPROVED,
        SocialPostState.SCHEDULED,
        SocialPostState.CANCELLED,
    },
    SocialPostState.CONNECTION_REQUIRED: {
        SocialPostState.NEEDS_REVIEW,
        SocialPostState.APPROVED,
        SocialPostState.SCHEDULED,
        SocialPostState.CANCELLED,
    },
    SocialPostState.PUBLISHED: set(),
    SocialPostState.CANCELLED: set(),
}


class InvalidStateTransition(ValidationError):
    pass


class ProviderWebhookReplayError(ValidationError):
    pass


@dataclass(frozen=True)
class JobClaim:
    job_id: uuid.UUID
    claim_token: uuid.UUID


def _media_snapshot(variant):
    return [
        {
            "asset_id": str(asset.id),
            "asset_type": asset.asset_type,
            "storage_url": asset.publish_url,
            "publish_storage_key": asset.publish_storage_key,
            "content_type": asset.content_type,
            "byte_size": asset.byte_size,
            "checksum_sha256": asset.checksum_sha256,
            "width": asset.width,
            "height": asset.height,
            "duration_ms": asset.duration_ms,
            "alt_text": asset.alt_text,
            "metadata": asset.metadata,
        }
        for asset in variant.media_assets.order_by("sort_order", "created_at")
    ]


def create_version(variant, *, approved_by=None, approved_at=None, quality_analyzer=None):
    from integrations.social.services.quality import build_quality_report

    latest = variant.versions.order_by("-version").first()
    media_snapshot = _media_snapshot(variant)
    quality_check = build_quality_report(
        variant=variant,
        copy=variant.copy,
        hashtags=list(variant.hashtags),
        metadata=dict(variant.metadata),
        media_snapshot=media_snapshot,
        analyzer=quality_analyzer,
    )
    return SocialPostVersion.objects.create(
        variant=variant,
        version=(latest.version + 1) if latest else 1,
        copy=variant.copy,
        hashtags=list(variant.hashtags),
        metadata=dict(variant.metadata),
        media_snapshot=media_snapshot,
        quality_check=quality_check,
        scheduled_for=variant.scheduled_for,
        approved_by=approved_by,
        approved_at=approved_at,
    )


def _set_variant_state(variant, state, *, failure_message=""):
    if state != variant.status and state not in VARIANT_TRANSITIONS.get(variant.status, set()):
        raise InvalidStateTransition(f"Cannot move a post from {variant.status} to {state}.")
    variant.status = state
    variant.failure_reason = failure_message
    variant.save(update_fields=["status", "failure_reason", "updated_at"])
    variant.post.state = state
    variant.post.save(update_fields=["state", "updated_at"])


@transaction.atomic
def transition_variant(variant, state, *, failure_message=""):
    variant = SocialPostVariant.objects.select_for_update().select_related("post").get(pk=variant.pk)
    _set_variant_state(variant, SocialPostState(state), failure_message=failure_message)
    return variant


@transaction.atomic
def edit_variant(
    variant,
    *,
    copy=None,
    hashtags=None,
    scheduled_for=None,
    metadata=None,
    media_changed=False,
):
    variant = SocialPostVariant.objects.select_for_update().get(pk=variant.pk)
    if variant.status not in EDITABLE_STATES:
        raise InvalidStateTransition("This post can no longer be edited.")
    changed = media_changed
    if copy is not None and copy != variant.copy:
        variant.copy = copy
        changed = True
    if hashtags is not None and list(hashtags) != list(variant.hashtags):
        variant.hashtags = list(hashtags)
        changed = True
    if scheduled_for is not None and scheduled_for != variant.scheduled_for:
        variant.scheduled_for = scheduled_for
        changed = True
    if metadata is not None and dict(metadata) != dict(variant.metadata):
        variant.metadata = dict(metadata)
        changed = True
    if not changed:
        return variant

    variant.approved_version = None
    variant.status = (
        SocialPostState.DRAFT
        if variant.status == SocialPostState.DRAFT
        else SocialPostState.NEEDS_REVIEW
    )
    variant.failure_reason = ""
    variant.save(update_fields=[
        "copy", "hashtags", "scheduled_for", "metadata", "approved_version", "status",
        "failure_reason", "updated_at",
    ])
    variant.post.state = (
        SocialPostState.DRAFT
        if variant.status == SocialPostState.DRAFT
        else SocialPostState.NEEDS_REVIEW
    )
    variant.post.save(update_fields=["state", "updated_at"])
    create_version(variant)
    variant.publish_jobs.filter(
        status__in={
            PublishJobState.DRAFT,
            PublishJobState.NEEDS_REVIEW,
            PublishJobState.APPROVED,
            PublishJobState.SCHEDULED,
            PublishJobState.FAILED,
            PublishJobState.CONNECTION_REQUIRED,
        }
    ).update(
        status=PublishJobState.CANCELLED,
        completed_at=timezone.now(),
        failure_message="Approval was revoked because the scheduled content changed.",
        claim_token=None,
        claimed_at=None,
    )
    return variant


@transaction.atomic
def approve_variant(variant, *, approved_by=None):
    variant = SocialPostVariant.objects.select_for_update().get(pk=variant.pk)
    if variant.status not in {
        SocialPostState.DRAFT,
        SocialPostState.NEEDS_REVIEW,
        SocialPostState.FAILED,
        SocialPostState.CONNECTION_REQUIRED,
    }:
        raise InvalidStateTransition("Only draft, review, failed, or disconnected posts can be approved.")
    from integrations.social.services.publishing_routing import selected_provider

    adapter = publishing_provider_registry.create(selected_provider(variant.post.workspace))
    validate_variant_media(variant, adapter.capabilities)
    version = create_version(
        variant,
        approved_by=approved_by,
        approved_at=timezone.now(),
    )
    from integrations.social.services.quality import hard_block_messages

    blockers = hard_block_messages(version.quality_check)
    if blockers:
        raise ValidationError({"quality": blockers})
    variant.approved_version = version
    variant.status = SocialPostState.APPROVED
    variant.failure_reason = ""
    variant.save(update_fields=["approved_version", "status", "failure_reason", "updated_at"])
    variant.post.state = SocialPostState.APPROVED
    variant.post.save(update_fields=["state", "updated_at"])
    return version


@transaction.atomic
def cancel_variant(variant):
    variant = SocialPostVariant.objects.select_for_update().select_related("post").get(pk=variant.pk)
    if variant.status in {
        SocialPostState.PUBLISHING,
        SocialPostState.SUBMITTED,
        SocialPostState.PUBLISHED,
        SocialPostState.CANCELLED,
    }:
        raise InvalidStateTransition("This post can no longer be cancelled before submission.")
    now = timezone.now()
    variant.publish_jobs.exclude(status__in=TERMINAL_STATES).update(
        status=PublishJobState.CANCELLED,
        completed_at=now,
        claim_token=None,
        claimed_at=None,
        failure_message="Publishing was cancelled before submission.",
    )
    _set_variant_state(variant, SocialPostState.CANCELLED)
    record_audit_event(
        workspace=variant.post.workspace,
        event_type=SocialAuditEventType.PUBLISH_CANCELLED,
        target=variant,
        details={"network": variant.network},
    )
    return variant


def _provider_request(job, attempt):
    version = job.approved_version
    connection = job.connection
    adapter = publishing_provider_registry.create(ProviderName(job.provider))
    validate_version_media(version, adapter.capabilities)
    account = SocialAccount(
        provider_profile_id=connection.provider_profile_id,
        provider_account_id=connection.provider_account_id,
        network=PublishingNetwork(job.variant.network),
        display_name=connection.display_name,
        account_type=connection.account_type,
        capabilities=adapter.capabilities,
    )
    media = tuple(
        PostMedia(
            media_type=PublishingMediaType(item["asset_type"]),
            storage_url=safe_publish_url(item["storage_url"]),
            alt_text=str(item.get("alt_text") or ""),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )
        for item in version.media_snapshot
        if item.get("asset_type") in PublishingMediaType.values and item.get("storage_url")
    )
    post = NormalizedPost(
        network=PublishingNetwork(job.variant.network),
        text=version.copy,
        hashtags=tuple(version.hashtags),
        media=media,
        scheduled_for=version.scheduled_for,
    )
    return adapter, PublishNowRequest(
        workspace_id=job.variant.post.workspace_id,
        idempotency_key=str(attempt.idempotency_key),
        account=account,
        post=post,
    )


def _diagnostics(error):
    details = dict(getattr(error, "safe_details", {}) or {})
    details["category"] = getattr(error, "category", ProviderErrorCategory.PERMANENT_FAILURE).value
    return details


def _retry_delay(attempt_count):
    base = django_settings.SOCIAL_PUBLISH_RETRY_BASE_SECONDS
    maximum = django_settings.SOCIAL_PUBLISH_RETRY_MAX_SECONDS
    return min(maximum, base * (2 ** max(0, attempt_count - 1)))


def _retry_delay_for_error(attempt_count, error):
    requested = getattr(error, "retry_after_seconds", None) or 0
    return min(
        django_settings.SOCIAL_PUBLISH_RETRY_MAX_SECONDS,
        max(_retry_delay(attempt_count), requested),
    )


def _human_failure(error):
    if isinstance(error, ProviderValidationError):
        return "The post does not meet the selected social network's publishing requirements."
    if isinstance(error, ProviderAuthenticationError):
        return "Reconnect the social account before publishing."
    if isinstance(error, ProviderConfigurationError):
        return "Publishing is not configured for this workspace."
    if isinstance(error, (ProviderRateLimitError, ProviderTemporaryFailureError)):
        return "The publishing service is temporarily unavailable."
    return "The post could not be published."


def _error_from_result(error_info):
    category = error_info.category if error_info else ProviderErrorCategory.PERMANENT_FAILURE
    message = error_info.message if error_info else "The publishing request failed."
    details = error_info.safe_details if error_info else {}
    retry_after = error_info.retry_after_seconds if error_info else None
    error_types = {
        ProviderErrorCategory.CONFIGURATION: ProviderConfigurationError,
        ProviderErrorCategory.AUTHENTICATION: ProviderAuthenticationError,
        ProviderErrorCategory.VALIDATION: ProviderValidationError,
        ProviderErrorCategory.RATE_LIMIT: ProviderRateLimitError,
        ProviderErrorCategory.TEMPORARY_FAILURE: ProviderTemporaryFailureError,
        ProviderErrorCategory.PERMANENT_FAILURE: ProviderPermanentFailureError,
        ProviderErrorCategory.UNKNOWN_OUTCOME: ProviderUnknownOutcomeError,
    }
    return error_types[category](
        message,
        safe_details=details,
        retry_after_seconds=retry_after,
    )


@transaction.atomic
def claim_due_jobs(*, now=None, limit=100):
    now = now or timezone.now()
    provider_values = [provider.value for provider in ProviderName]
    candidates = list(
        PublishJob.objects.filter(
            provider__in=provider_values,
            status=PublishJobState.SCHEDULED,
            scheduled_for__lte=now,
            attempt_count__lt=django_settings.SOCIAL_PUBLISH_MAX_ATTEMPTS,
        )
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by("scheduled_for", "created_at")
        .values_list("id", flat=True)[:limit]
    )
    claims = []
    for job_id in candidates:
        token = uuid.uuid4()
        claimed = (
            PublishJob.objects.filter(
                pk=job_id,
                status=PublishJobState.SCHEDULED,
                scheduled_for__lte=now,
                attempt_count__lt=django_settings.SOCIAL_PUBLISH_MAX_ATTEMPTS,
            )
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .update(
                status=PublishJobState.PUBLISHING,
                claim_token=token,
                claimed_at=now,
                failure_message="",
                diagnostic_details={},
            )
        )
        if claimed:
            job = PublishJob.objects.select_related("variant__post").get(pk=job_id)
            variant = job.variant
            _set_variant_state(variant, SocialPostState.PUBLISHING)
            record_audit_event(
                workspace=variant.post.workspace,
                event_type=SocialAuditEventType.PUBLISH_STARTED,
                target=job,
                details={"provider": job.provider, "attempt_number": job.attempt_count + 1},
            )
            claims.append(JobClaim(job_id=job_id, claim_token=token))
    return tuple(claims)


def execute_claimed_job(claim):
    with transaction.atomic():
        job = (
            PublishJob.objects.select_for_update()
            .select_related("variant__post", "approved_version", "connection")
            .get(pk=claim.job_id)
        )
        if job.status != PublishJobState.PUBLISHING or job.claim_token != claim.claim_token:
            return None
        if job.connection.status != ConnectionState.CONNECTED:
            job.status = PublishJobState.CONNECTION_REQUIRED
            job.failure_message = "Reconnect the social account before publishing."
            job.completed_at = timezone.now()
            job.claim_token = None
            job.claimed_at = None
            job.save(update_fields=[
                "status", "failure_message", "completed_at", "claim_token", "claimed_at", "updated_at",
            ])
            _set_variant_state(
                job.variant,
                SocialPostState.CONNECTION_REQUIRED,
                failure_message=job.failure_message,
            )
            record_audit_event(
                workspace=job.variant.post.workspace,
                event_type=SocialAuditEventType.PUBLISH_FAILED,
                target=job,
                details={"reason": "connection_required", "provider": job.provider},
            )
            return job
        if job.approved_version_id != job.variant.approved_version_id:
            job.status = PublishJobState.CANCELLED
            job.failure_message = "Approval was revoked before publishing."
            job.completed_at = timezone.now()
            job.claim_token = None
            job.save(update_fields=[
                "status", "failure_message", "completed_at", "claim_token", "updated_at",
            ])
            return job
        attempt = PublishAttempt.objects.create(
            job=job,
            attempt_number=job.attempt_count + 1,
        )
        job.attempt_count = attempt.attempt_number
        job.save(update_fields=["attempt_count", "updated_at"])

    try:
        adapter, request = _provider_request(job, attempt)
        result = adapter.publish_now(request)
    except ProviderUnknownOutcomeError as error:
        return _finish_unknown(job.id, claim.claim_token, attempt.id, error)
    except (ProviderRateLimitError, ProviderTemporaryFailureError) as error:
        return _finish_retryable_failure(job.id, claim.claim_token, attempt.id, error)
    except PublishingProviderError as error:
        return _finish_permanent_failure(job.id, claim.claim_token, attempt.id, error)

    if result.outcome == PublishOutcome.PUBLISHED:
        return _finish_success(job.id, claim.claim_token, attempt.id, result, published=True)
    if result.outcome == PublishOutcome.ACCEPTED:
        return _finish_success(job.id, claim.claim_token, attempt.id, result, published=False)
    if result.outcome == PublishOutcome.UNKNOWN:
        error = ProviderUnknownOutcomeError(
            "The publishing outcome is unknown; check status before retrying."
        )
        return _finish_unknown(job.id, claim.claim_token, attempt.id, error, result=result)
    error = _error_from_result(result.error)
    if isinstance(error, ProviderUnknownOutcomeError):
        return _finish_unknown(job.id, claim.claim_token, attempt.id, error, result=result)
    if isinstance(error, (ProviderRateLimitError, ProviderTemporaryFailureError)):
        return _finish_retryable_failure(job.id, claim.claim_token, attempt.id, error)
    return _finish_permanent_failure(job.id, claim.claim_token, attempt.id, error)


def _locked_job_and_attempt(job_id, claim_token, attempt_id):
    job = PublishJob.objects.select_for_update().select_related("variant").get(pk=job_id)
    if job.claim_token != claim_token or job.status != PublishJobState.PUBLISHING:
        return job, None
    return job, PublishAttempt.objects.select_for_update().get(pk=attempt_id, job=job)


@transaction.atomic
def _finish_success(job_id, claim_token, attempt_id, result, *, published):
    job, attempt = _locked_job_and_attempt(job_id, claim_token, attempt_id)
    if attempt is None:
        return job
    now = timezone.now()
    state = PublishJobState.PUBLISHED if published else PublishJobState.SUBMITTED
    job.status = state
    job.external_id = result.external_id
    job.submitted_at = job.submitted_at or now
    job.completed_at = now if published else None
    job.claim_token = None
    job.claimed_at = None
    job.unknown_since = None
    job.failure_message = ""
    job.diagnostic_details = {"provider_status": result.provider_status}
    job.save()
    attempt.status = state
    attempt.external_id = result.external_id
    attempt.completed_at = now
    attempt.diagnostic_details = {"provider_status": result.provider_status}
    attempt.save()
    _set_variant_state(
        job.variant,
        SocialPostState.PUBLISHED if published else SocialPostState.SUBMITTED,
    )
    record_audit_event(
        workspace=job.variant.post.workspace,
        event_type=SocialAuditEventType.PUBLISH_COMPLETED if published else SocialAuditEventType.PUBLISH_SUBMITTED,
        target=job,
        details={"provider": job.provider, "attempt_number": job.attempt_count, "provider_status": result.provider_status},
    )
    return job


@transaction.atomic
def _finish_unknown(job_id, claim_token, attempt_id, error, *, result=None):
    job, attempt = _locked_job_and_attempt(job_id, claim_token, attempt_id)
    if attempt is None:
        return job
    now = timezone.now()
    external_id = result.external_id if result else ""
    job.status = PublishJobState.UNKNOWN
    job.external_id = external_id or job.external_id
    job.submitted_at = job.submitted_at or now
    job.unknown_since = now
    job.claim_token = None
    job.claimed_at = None
    job.failure_message = "Publication was submitted, but its result has not been confirmed."
    job.diagnostic_details = _diagnostics(error)
    job.save()
    attempt.status = PublishJobState.UNKNOWN
    attempt.external_id = external_id
    attempt.failure_message = job.failure_message
    attempt.diagnostic_details = job.diagnostic_details
    attempt.completed_at = now
    attempt.save()
    _set_variant_state(job.variant, SocialPostState.SUBMITTED)
    record_audit_event(
        workspace=job.variant.post.workspace,
        event_type=SocialAuditEventType.PUBLISH_SUBMITTED,
        target=job,
        details={"provider": job.provider, "attempt_number": job.attempt_count, "outcome": "unknown"},
    )
    return job


@transaction.atomic
def _finish_retryable_failure(job_id, claim_token, attempt_id, error):
    job, attempt = _locked_job_and_attempt(job_id, claim_token, attempt_id)
    if attempt is None:
        return job
    now = timezone.now()
    retry = job.attempt_count < django_settings.SOCIAL_PUBLISH_MAX_ATTEMPTS
    job.status = PublishJobState.SCHEDULED if retry else PublishJobState.FAILED
    job.next_attempt_at = (
        now + timedelta(seconds=_retry_delay_for_error(job.attempt_count, error))
        if retry
        else None
    )
    job.completed_at = None if retry else now
    job.claim_token = None
    job.claimed_at = None
    job.failure_message = _human_failure(error)
    job.diagnostic_details = _diagnostics(error)
    job.save()
    attempt.status = PublishJobState.FAILED
    attempt.failure_message = job.failure_message
    attempt.diagnostic_details = job.diagnostic_details
    attempt.completed_at = now
    attempt.save()
    _set_variant_state(
        job.variant,
        SocialPostState.SCHEDULED if retry else SocialPostState.FAILED,
        failure_message=job.failure_message,
    )
    record_audit_event(
        workspace=job.variant.post.workspace,
        event_type=SocialAuditEventType.PUBLISH_FAILED,
        target=job,
        details={"provider": job.provider, "attempt_number": job.attempt_count, "retry_scheduled": retry},
    )
    return job


@transaction.atomic
def _finish_permanent_failure(job_id, claim_token, attempt_id, error):
    job, attempt = _locked_job_and_attempt(job_id, claim_token, attempt_id)
    if attempt is None:
        return job
    now = timezone.now()
    connection_required = isinstance(error, ProviderAuthenticationError)
    job.status = (
        PublishJobState.CONNECTION_REQUIRED if connection_required else PublishJobState.FAILED
    )
    job.completed_at = now
    job.claim_token = None
    job.claimed_at = None
    job.failure_message = _human_failure(error)
    job.diagnostic_details = _diagnostics(error)
    job.save()
    attempt.status = job.status
    attempt.failure_message = job.failure_message
    attempt.diagnostic_details = job.diagnostic_details
    attempt.completed_at = now
    attempt.save()
    _set_variant_state(
        job.variant,
        SocialPostState.CONNECTION_REQUIRED if connection_required else SocialPostState.FAILED,
        failure_message=job.failure_message,
    )
    record_audit_event(
        workspace=job.variant.post.workspace,
        event_type=SocialAuditEventType.PUBLISH_FAILED,
        target=job,
        details={"provider": job.provider, "attempt_number": job.attempt_count, "connection_required": connection_required},
    )
    return job


def reconcile_job(job):
    adapter = publishing_provider_registry.create(ProviderName(job.provider))
    attempt = job.attempts.order_by("-attempt_number").first()
    lookup_id = job.external_id or (str(attempt.idempotency_key) if attempt else "")
    result = adapter.get_publish_status(GetPublishStatusRequest(
        workspace_id=job.variant.post.workspace_id,
        external_id=lookup_id,
        idempotency_key=str(attempt.idempotency_key) if attempt else "",
    ))
    with transaction.atomic():
        job = PublishJob.objects.select_for_update().select_related("variant").get(pk=job.pk)
        if job.status not in RECONCILE_STATES:
            return job
        previous_status = job.status
        now = timezone.now()
        if result.outcome == PublishOutcome.PUBLISHED:
            job.status = PublishJobState.PUBLISHED
            job.external_id = result.external_id or job.external_id
            job.completed_at = now
            job.failure_message = ""
            job.diagnostic_details = {"provider_status": result.provider_status}
            _set_variant_state(job.variant, SocialPostState.PUBLISHED)
        elif result.outcome == PublishOutcome.FAILED:
            job.status = PublishJobState.FAILED
            job.completed_at = now
            job.failure_message = "The publishing service reported that the post failed."
            job.diagnostic_details = {"provider_status": result.provider_status}
            _set_variant_state(job.variant, SocialPostState.FAILED, failure_message=job.failure_message)
        elif result.outcome == PublishOutcome.ACCEPTED:
            job.status = PublishJobState.SUBMITTED
            job.failure_message = ""
            job.diagnostic_details = {"provider_status": result.provider_status}
            _set_variant_state(job.variant, SocialPostState.SUBMITTED)
        elif (
            job.status == PublishJobState.UNKNOWN
            and job.attempt_count < django_settings.SOCIAL_PUBLISH_MAX_ATTEMPTS
        ):
            job.status = PublishJobState.SCHEDULED
            job.next_attempt_at = now + timedelta(seconds=_retry_delay(job.attempt_count))
            job.failure_message = "The previous attempt was not found; publishing will be retried."
            _set_variant_state(job.variant, SocialPostState.SCHEDULED)
        elif job.status == PublishJobState.UNKNOWN:
            job.status = PublishJobState.FAILED
            job.completed_at = now
            job.next_attempt_at = None
            job.failure_message = "The publishing result could not be confirmed after the allowed attempts."
            job.diagnostic_details = {"provider_status": result.provider_status}
            _set_variant_state(
                job.variant,
                SocialPostState.FAILED,
                failure_message=job.failure_message,
            )
        job.save()
        if job.status != previous_status:
            event_type = {
                PublishJobState.PUBLISHED: SocialAuditEventType.PUBLISH_COMPLETED,
                PublishJobState.SUBMITTED: SocialAuditEventType.PUBLISH_SUBMITTED,
                PublishJobState.FAILED: SocialAuditEventType.PUBLISH_FAILED,
                PublishJobState.SCHEDULED: SocialAuditEventType.PUBLISH_SUBMITTED,
            }.get(job.status)
            if event_type:
                record_audit_event(
                    workspace=job.variant.post.workspace,
                    event_type=event_type,
                    target=job,
                    details={
                        "provider": job.provider,
                        "reconciled": True,
                        "status": job.status,
                    },
                )
        return job


def reconcile_pending_jobs(*, limit=100):
    now = timezone.now()
    stale_before = now - timedelta(seconds=django_settings.SOCIAL_PUBLISH_CLAIM_TTL_SECONDS)
    stale_job_ids = list(PublishJob.objects.filter(
        status=PublishJobState.PUBLISHING,
        claimed_at__lt=stale_before,
    ).values_list("id", flat=True)[:limit])
    if stale_job_ids:
        with transaction.atomic():
            stale_jobs = list(
                PublishJob.objects.select_for_update()
                .select_related("variant__post")
                .filter(id__in=stale_job_ids, status=PublishJobState.PUBLISHING)
            )
            for job in stale_jobs:
                job.status = PublishJobState.UNKNOWN
                job.unknown_since = now
                job.claim_token = None
                job.claimed_at = None
                job.failure_message = "Publication was interrupted before its result was confirmed."
                job.save()
                job.attempts.filter(status=PublishJobState.PUBLISHING).update(
                    status=PublishJobState.UNKNOWN,
                    completed_at=now,
                    failure_message=job.failure_message,
                )
                _set_variant_state(job.variant, SocialPostState.SUBMITTED)
    jobs = list(
        PublishJob.objects.select_related("variant__post")
        .filter(status__in=RECONCILE_STATES)
        .order_by("submitted_at", "created_at")[:limit]
    )
    counts = {"published": 0, "failed": 0, "pending": 0, "retried": 0}
    for job in jobs:
        try:
            before = job.status
            reconciled = reconcile_job(job)
            if reconciled.status == PublishJobState.PUBLISHED:
                counts["published"] += 1
            elif reconciled.status == PublishJobState.FAILED:
                counts["failed"] += 1
            elif before == PublishJobState.UNKNOWN and reconciled.status == PublishJobState.SCHEDULED:
                counts["retried"] += 1
            else:
                counts["pending"] += 1
        except (PublishingProviderError, ValueError):
            counts["pending"] += 1
    return counts


@transaction.atomic
def process_provider_webhook(*, provider, request: WebhookRequest):
    provider_name = ProviderName(provider)
    adapter = publishing_provider_registry.create(provider_name)
    parsed = adapter.verify_and_parse_webhook(request)
    processed = []
    for event in parsed.events:
        event_id = str(
            event.safe_metadata.get("event_id")
            or event.safe_metadata.get("delivery_id")
            or ""
        )
        if not event_id:
            raise ProviderValidationError("The webhook event identifier is missing.")
        if ProviderEvent.objects.filter(
            provider=provider_name.value,
            external_event_id=event_id,
        ).exists():
            raise ProviderWebhookReplayError("The webhook event has already been processed.")

        profile_id = str(event.safe_metadata.get("profile_id") or "")
        account_id = str(event.safe_metadata.get("account_id") or "")
        if not profile_id and not account_id:
            raise ProviderAuthenticationError(
                "The webhook does not identify a social account connection."
            )
        connection_query = Q(provider=provider_name.value)
        if profile_id:
            connection_query &= Q(provider_profile_id=profile_id)
        if account_id:
            connection_query &= Q(provider_account_id=account_id)
        connections = list(SocialConnection.objects.filter(connection_query)[:2])
        if len(connections) != 1:
            raise ProviderAuthenticationError(
                "The webhook does not belong to a known social account connection."
            )
        connection = connections[0]
        jobs = PublishJob.objects.select_for_update().filter(
            variant__post__workspace=connection.workspace,
            provider=provider_name.value,
        )
        job = None
        if event.external_id:
            job = jobs.filter(external_id=event.external_id).order_by("-created_at").first()
        if job is None and event.idempotency_key:
            job = jobs.filter(
                attempts__idempotency_key=event.idempotency_key
            ).order_by("-created_at").first()
        now = timezone.now()
        if job:
            if event.outcome == PublishOutcome.PUBLISHED:
                job.status = PublishJobState.PUBLISHED
                job.completed_at = now
                job.failure_message = ""
                _set_variant_state(job.variant, SocialPostState.PUBLISHED)
            elif event.outcome == PublishOutcome.FAILED:
                job.status = PublishJobState.FAILED
                job.completed_at = now
                job.failure_message = "The publishing service reported that the post failed."
                _set_variant_state(job.variant, SocialPostState.FAILED, failure_message=job.failure_message)
            elif event.outcome == PublishOutcome.ACCEPTED:
                job.status = PublishJobState.SUBMITTED
                job.submitted_at = job.submitted_at or now
                _set_variant_state(job.variant, SocialPostState.SUBMITTED)
            job.diagnostic_details = {
                "event_type": event.event_type,
                "provider_status": event.outcome.value,
            }
            job.save()
            event_type = {
                PublishOutcome.PUBLISHED: SocialAuditEventType.PUBLISH_COMPLETED,
                PublishOutcome.FAILED: SocialAuditEventType.PUBLISH_FAILED,
                PublishOutcome.ACCEPTED: SocialAuditEventType.PUBLISH_SUBMITTED,
            }.get(event.outcome)
            if event_type:
                record_audit_event(
                    workspace=connection.workspace,
                    event_type=event_type,
                    target=job,
                    details={
                        "provider": provider_name.value,
                        "webhook": True,
                        "status": job.status,
                    },
                )
        try:
            stored_payload = {
                key: value
                for key, value in dict(event.safe_metadata).items()
                if key not in {"profile_id", "account_id"}
            }
            stored_payload["connection_id"] = str(connection.id)
            provider_event = ProviderEvent.objects.create(
                workspace=connection.workspace,
                connection=connection,
                publish_job=job,
                provider=provider_name.value,
                external_event_id=event_id,
                event_type=event.event_type,
                normalized_status=job.status if job else None,
                payload=stored_payload,
                occurred_at=event.occurred_at,
            )
        except IntegrityError as exc:
            raise ProviderWebhookReplayError(
                "The webhook event has already been processed."
            ) from exc
        processed.append(provider_event)
    return tuple(processed)
