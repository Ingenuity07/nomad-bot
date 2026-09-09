import logging
import json

from celery import shared_task

from integrations.social.services.lifecycle import (
    claim_due_jobs,
    execute_claimed_job,
    reconcile_pending_jobs,
)
from integrations.social.services.analytics import refresh_published_metrics


logger = logging.getLogger(__name__)


@shared_task(name="social.publish_due_jobs")
def publish_due_jobs():
    counts = {
        "claimed": 0,
        "published": 0,
        "submitted": 0,
        "failed": 0,
        "connection_required": 0,
        "unknown": 0,
        "rescheduled": 0,
    }
    claims = claim_due_jobs()
    counts["claimed"] = len(claims)
    for claim in claims:
        try:
            job = execute_claimed_job(claim)
        except Exception:
            logger.exception("Could not execute social publish job %s", claim.job_id)
            continue
        if job is None:
            continue
        key = {
            "PUBLISHED": "published",
            "SUBMITTED": "submitted",
            "FAILED": "failed",
            "CONNECTION_REQUIRED": "connection_required",
            "UNKNOWN": "unknown",
            "SCHEDULED": "rescheduled",
        }.get(job.status)
        if key:
            counts[key] += 1
    logger.info("social_publish_batch %s", json.dumps(counts, sort_keys=True))
    if counts["failed"] or counts["unknown"]:
        logger.warning("social_publish_batch_needs_attention %s", json.dumps(counts, sort_keys=True))
    return counts


@shared_task(name="social.reconcile_publish_jobs")
def reconcile_publish_jobs():
    counts = reconcile_pending_jobs()
    logger.info("social_reconcile_batch %s", json.dumps(counts, sort_keys=True))
    return counts


@shared_task(name="social.refresh_post_metrics")
def refresh_post_metrics():
    counts = refresh_published_metrics()
    logger.info("social_metrics_batch %s", json.dumps(counts, sort_keys=True))
    return counts
