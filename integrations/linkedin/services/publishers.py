import hashlib
import hmac
import json

import requests
from django.conf import settings


class PublisherConfigurationError(RuntimeError):
    pass


def publisher_status(automation_settings):
    if automation_settings.publisher == automation_settings.WEBHOOK:
        return {
            "ready": bool(settings.LINKEDIN_PUBLISH_WEBHOOK_URL),
            "mode": "webhook",
            "label": "Approved provider webhook",
            "detail": "Connected" if settings.LINKEDIN_PUBLISH_WEBHOOK_URL else "Add LINKEDIN_PUBLISH_WEBHOOK_URL",
        }
    return {
        "ready": True,
        "mode": "manual",
        "label": "Manual handoff",
        "detail": "Due posts move to Ready for posting",
    }


class LinkedInPublisher:
    def publish(self, post):
        if post.settings.publisher == post.settings.MANUAL:
            raise PublisherConfigurationError("Manual handoff is selected; the post is ready to copy into LinkedIn.")
        if not settings.LINKEDIN_PUBLISH_WEBHOOK_URL:
            raise PublisherConfigurationError("LINKEDIN_PUBLISH_WEBHOOK_URL is not configured.")

        payload = {
            "event": "linkedin.post.due",
            "idempotency_key": str(post.id),
            "page_name": post.settings.page_name,
            "text": f"{post.body}\n\n{' '.join(post.hashtags)}".strip(),
            "image_url": post.image_url,
            "image_prompt": post.image_prompt,
            "alt_text": post.alt_text,
            "scheduled_for": post.scheduled_for.isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Idempotency-Key": str(post.id)}
        if settings.LINKEDIN_PUBLISH_WEBHOOK_SECRET:
            headers["X-Nomad-Signature"] = hmac.new(
                settings.LINKEDIN_PUBLISH_WEBHOOK_SECRET.encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
        response = requests.post(
            settings.LINKEDIN_PUBLISH_WEBHOOK_URL,
            data=body,
            headers=headers,
            timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {}
        return str(data.get("post_id") or data.get("id") or post.id), data

