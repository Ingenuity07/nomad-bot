import logging

from celery import shared_task
from django.utils import timezone

from .models import LinkedInAutomationSettings, LinkedInPost
from .services.publishers import BufferPublisher, LinkedInPublisher
from .services.scheduler import fill_queue

logger = logging.getLogger(__name__)


def publish_post(post):
    """Send one claimed post and persist the publisher's truthful lifecycle state."""
    try:
        result = LinkedInPublisher().publish(post)
        post.status = getattr(LinkedInPost, result.state, LinkedInPost.SUBMITTED)
        post.external_post_id = result.external_id
        post.published_at = timezone.now() if post.status == LinkedInPost.PUBLISHED else None
        post.failure_reason = ""
        post.generation_metadata = {**post.generation_metadata, "publish_result": result.raw}
    except Exception as exc:
        post.status = LinkedInPost.FAILED
        post.failure_reason = str(exc)
    post.save()
    return post


@shared_task(name="linkedin.fill_content_queues")
def fill_content_queues():
    generated = 0
    for settings in LinkedInAutomationSettings.objects.filter(is_active=True):
        try:
            generated += len(fill_queue(settings))
        except Exception:
            logger.exception("Could not fill LinkedIn queue for settings %s", settings.id)
    return {"generated": generated}


@shared_task(name="linkedin.publish_due_posts")
def publish_due_posts():
    due = LinkedInPost.objects.select_related("settings").filter(
        status=LinkedInPost.SCHEDULED,
        scheduled_for__lte=timezone.now(),
        settings__is_active=True,
    )
    published = submitted = ready = failed = 0
    for post in due:
        if post.settings.publisher == post.settings.MANUAL:
            if LinkedInPost.objects.filter(pk=post.pk, status=LinkedInPost.SCHEDULED).update(status=LinkedInPost.READY, failure_reason=""):
                ready += 1
            continue
        claimed = LinkedInPost.objects.filter(pk=post.pk, status=LinkedInPost.SCHEDULED).update(status=LinkedInPost.PUBLISHING)
        if not claimed:
            continue
        post.refresh_from_db()
        publish_post(post)
        if post.status == LinkedInPost.PUBLISHED:
            published += 1
        elif post.status == LinkedInPost.SUBMITTED:
            submitted += 1
        else:
            failed += 1
    return {"published": published, "submitted": submitted, "ready": ready, "failed": failed}


@shared_task(name="linkedin.sync_submitted_posts")
def sync_submitted_posts():
    """Confirm Buffer delivery instead of treating API acceptance as publication."""
    posts = LinkedInPost.objects.select_related("settings").filter(
        status=LinkedInPost.SUBMITTED,
        settings__publisher=LinkedInAutomationSettings.BUFFER,
    )[:100]
    published = failed = pending = 0
    publisher = BufferPublisher()
    for post in posts:
        try:
            remote_status, result = publisher.status(post.external_post_id)
            post.generation_metadata = {**post.generation_metadata, "publisher_status_result": result}
            if remote_status == "sent":
                post.status = LinkedInPost.PUBLISHED
                post.published_at = timezone.now()
                post.failure_reason = ""
                published += 1
            elif remote_status in {"error", "failed"}:
                post.status = LinkedInPost.FAILED
                post.failure_reason = "Buffer could not publish this post. Open Buffer for the channel error."
                failed += 1
            else:
                pending += 1
            post.save()
        except Exception:
            pending += 1
            logger.exception("Could not sync Buffer post %s", post.external_post_id)
    return {"published": published, "failed": failed, "pending": pending}
