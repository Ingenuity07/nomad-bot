import logging

from celery import shared_task
from django.utils import timezone

from .models import LinkedInAutomationSettings, LinkedInPost
from .services.publishers import LinkedInPublisher
from .services.scheduler import fill_queue

logger = logging.getLogger(__name__)


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
    published = ready = failed = 0
    for post in due:
        if post.settings.publisher == post.settings.MANUAL:
            if LinkedInPost.objects.filter(pk=post.pk, status=LinkedInPost.SCHEDULED).update(status=LinkedInPost.READY, failure_reason=""):
                ready += 1
            continue
        claimed = LinkedInPost.objects.filter(pk=post.pk, status=LinkedInPost.SCHEDULED).update(status=LinkedInPost.PUBLISHING)
        if not claimed:
            continue
        post.refresh_from_db()
        try:
            external_id, result = LinkedInPublisher().publish(post)
            post.status = LinkedInPost.PUBLISHED
            post.external_post_id = external_id
            post.published_at = timezone.now()
            post.failure_reason = ""
            post.generation_metadata = {**post.generation_metadata, "publish_result": result}
            published += 1
        except Exception as exc:
            post.status = LinkedInPost.FAILED
            post.failure_reason = str(exc)
            failed += 1
        post.save()
    return {"published": published, "ready": ready, "failed": failed}
