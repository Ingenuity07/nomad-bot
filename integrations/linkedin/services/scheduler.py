from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from ..models import ContentBrief, LinkedInAutomationSettings, LinkedInPost
from .content import LinkedInContentGenerator
from .images import OpenAIImageGenerator


def _zone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def upcoming_slots(settings, now=None, limit=None):
    now = now or timezone.now()
    local_now = now.astimezone(_zone(settings.timezone))
    days = settings.schedule_days or list(range(min(settings.posts_per_week, 5)))
    target_count = limit or settings.posts_per_week
    slots = []
    for offset in range(settings.queue_horizon_days + 1):
        day = local_now.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        local_slot = datetime.combine(day, settings.post_time, tzinfo=_zone(settings.timezone))
        slot = local_slot.astimezone(ZoneInfo("UTC"))
        if slot <= now + timedelta(minutes=5):
            continue
        if not LinkedInPost.objects.filter(settings=settings, scheduled_for=slot).exclude(status=LinkedInPost.CANCELLED).exists():
            slots.append(slot)
        if len(slots) >= target_count:
            break
    return slots


@transaction.atomic
def generate_post(settings, brief=None, scheduled_for=None, generator=None, image_generator=None):
    brief = brief or settings.briefs.filter(is_active=True).first()
    if not brief:
        raise ValueError("Add at least one active content brief before generating posts.")
    if scheduled_for is None:
        slots = upcoming_slots(settings, limit=1)
        if not slots:
            raise ValueError("No free schedule slot is available in the configured horizon.")
        scheduled_for = slots[0]
    sequence = settings.posts.count() + 1
    content = (generator or LinkedInContentGenerator()).generate(settings, brief, sequence)
    post = LinkedInPost.objects.create(
        settings=settings,
        brief=brief,
        topic=content.topic,
        hook=content.hook,
        body=content.body,
        hashtags=content.hashtags,
        image_prompt=content.image_prompt,
        alt_text=content.alt_text,
        status=LinkedInPost.SCHEDULED if settings.approval_mode == settings.AUTO_PUBLISH else LinkedInPost.DRAFT,
        scheduled_for=scheduled_for,
        generation_metadata={"provider": content.provider, "model": content.model},
    )
    try:
        image_url, metadata, image_data = (image_generator or OpenAIImageGenerator()).generate(post.id, content.image_prompt)
        post.image_url = image_url
        post.image_data = image_data
        post.generation_metadata = {**post.generation_metadata, "image": metadata}
        post.save(update_fields=["image_url", "image_data", "generation_metadata", "updated_at"])
    except Exception as exc:
        post.generation_metadata = {**post.generation_metadata, "image": {"status": "failed", "message": str(exc)}}
        post.save(update_fields=["generation_metadata", "updated_at"])
    return post


def fill_queue(settings):
    if not settings.is_active:
        return []
    queued = settings.posts.filter(
        status__in=[LinkedInPost.DRAFT, LinkedInPost.SCHEDULED],
        scheduled_for__gt=timezone.now(),
    ).count()
    needed = max(0, settings.posts_per_week - queued)
    slots = upcoming_slots(settings, limit=needed) if needed else []
    generated = []
    briefs = list(settings.briefs.filter(is_active=True))
    if not briefs:
        return generated
    for index, slot in enumerate(slots):
        generated.append(generate_post(settings, briefs[index % len(briefs)], slot))
    return generated
