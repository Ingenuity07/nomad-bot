from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from integrations.social.models import ConnectionState, PublishJob, PublishJobState


def operational_health_snapshot(*, now=None):
    """Return secret-free queue signals suitable for staff health checks."""
    now = now or timezone.now()
    overdue_before = now - timedelta(
        minutes=settings.SOCIAL_HEALTH_OVERDUE_JOB_MINUTES
    )
    stale_before = now - timedelta(seconds=settings.SOCIAL_PUBLISH_CLAIM_TTL_SECONDS)
    failed_since = now - timedelta(hours=24)
    overdue_jobs = PublishJob.objects.filter(
        status=PublishJobState.SCHEDULED,
        scheduled_for__lt=overdue_before,
    ).count()
    stale_claims = PublishJob.objects.filter(
        status=PublishJobState.PUBLISHING,
        claimed_at__lt=stale_before,
    ).count()
    unresolved_jobs = PublishJob.objects.filter(
        status__in=[PublishJobState.SUBMITTED, PublishJobState.UNKNOWN],
        updated_at__lt=overdue_before,
    ).count()
    recent_failures = PublishJob.objects.filter(
        status=PublishJobState.FAILED,
        updated_at__gte=failed_since,
    ).count()
    connections_needing_attention = PublishJob.objects.filter(
        status=PublishJobState.CONNECTION_REQUIRED,
    ).values("connection_id").distinct().count()
    inactive_connections = PublishJob.objects.filter(
        status__in=[PublishJobState.SCHEDULED, PublishJobState.CONNECTION_REQUIRED],
    ).exclude(connection__status=ConnectionState.CONNECTED).values("connection_id").distinct().count()

    alerts = []
    signals = (
        (overdue_jobs, "OVERDUE_JOBS", "Scheduled posts are overdue."),
        (stale_claims, "STALE_CLAIMS", "Publishing claims have exceeded their lease."),
        (unresolved_jobs, "UNRESOLVED_OUTCOMES", "Submitted posts need reconciliation."),
        (
            recent_failures >= settings.SOCIAL_HEALTH_FAILED_JOB_THRESHOLD,
            "FAILURE_SPIKE",
            "Publishing failures exceeded the configured threshold.",
        ),
        (
            connections_needing_attention or inactive_connections,
            "CONNECTIONS_NEED_ATTENTION",
            "Scheduled publishing is waiting for account reconnection.",
        ),
    )
    for active, code, message in signals:
        if active:
            alerts.append({"code": code, "severity": "warning", "message": message})
    return {
        "status": "DEGRADED" if alerts else "HEALTHY",
        "queue": {
            "overdue_jobs": overdue_jobs,
            "stale_claims": stale_claims,
            "unresolved_jobs": unresolved_jobs,
            "recent_failures_24h": recent_failures,
            "connections_needing_attention": max(
                connections_needing_attention,
                inactive_connections,
            ),
        },
        "alerts": alerts,
        "checked_at": now.isoformat(),
    }
