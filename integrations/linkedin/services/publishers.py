import hashlib
import hmac
import json
from dataclasses import dataclass

import requests
from django.conf import settings


class PublisherConfigurationError(RuntimeError):
    pass


class PublisherResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    external_id: str
    state: str
    raw: dict


def _missing(*pairs):
    return [name for name, value in pairs if not value]


def publisher_status(automation_settings):
    if automation_settings.publisher == automation_settings.BUFFER:
        missing = _missing(
            ("BUFFER_API_KEY", settings.BUFFER_API_KEY),
            ("BUFFER_CHANNEL_ID", settings.BUFFER_CHANNEL_ID),
        )
        return {
            "ready": not missing,
            "mode": "buffer",
            "label": "Buffer · LinkedIn Page",
            "detail": "Ready to publish through Buffer" if not missing else f"Add {', '.join(missing)}",
            "missing": missing,
            "target": "ORGANIZATION",
        }
    if automation_settings.publisher == automation_settings.N8N:
        missing = _missing(
            ("N8N_LINKEDIN_WEBHOOK_URL", settings.N8N_LINKEDIN_WEBHOOK_URL),
            ("N8N_LINKEDIN_WEBHOOK_SECRET", settings.N8N_LINKEDIN_WEBHOOK_SECRET),
        )
        target = settings.N8N_LINKEDIN_TARGET if settings.N8N_LINKEDIN_TARGET in {"PERSON", "ORGANIZATION"} else "PERSON"
        return {
            "ready": not missing,
            "mode": "n8n",
            "label": f"n8n · {'Personal profile' if target == 'PERSON' else 'LinkedIn Page'}",
            "detail": "Signed workflow handoff is configured" if not missing else f"Add {', '.join(missing)}",
            "missing": missing,
            "target": target,
        }
    if automation_settings.publisher == automation_settings.WEBHOOK:
        missing = _missing(("LINKEDIN_PUBLISH_WEBHOOK_URL", settings.LINKEDIN_PUBLISH_WEBHOOK_URL))
        return {
            "ready": not missing,
            "mode": "webhook",
            "label": "Legacy provider webhook",
            "detail": "Connected" if not missing else "Add LINKEDIN_PUBLISH_WEBHOOK_URL",
            "missing": missing,
            "target": "ORGANIZATION",
        }
    return {
        "ready": True,
        "mode": "manual",
        "label": "Manual handoff",
        "detail": "Due posts move to Ready for posting",
        "missing": [],
        "target": "MANUAL",
    }


def _post_text(post):
    return f"{post.body}\n\n{' '.join(post.hashtags)}".strip()


def _graphql(query, variables=None):
    response = requests.post(
        settings.BUFFER_API_URL,
        headers={
            "Authorization": f"Bearer {settings.BUFFER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise PublisherResponseError("Buffer returned a non-JSON response.") from exc
    if data.get("errors"):
        message = "; ".join(str(item.get("message", item)) for item in data["errors"])
        raise PublisherResponseError(f"Buffer API error: {message}")
    return data


class BufferPublisher:
    CREATE_POST = """
        mutation CreatePost($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess { post { id text dueAt status channelId } }
            ... on MutationError { message }
          }
        }
    """
    GET_POST = """
        query GetPost($input: PostInput!) {
          post(input: $input) { id status dueAt channelId }
        }
    """

    def publish(self, post):
        missing = _missing(
            ("BUFFER_API_KEY", settings.BUFFER_API_KEY),
            ("BUFFER_CHANNEL_ID", settings.BUFFER_CHANNEL_ID),
        )
        if missing:
            raise PublisherConfigurationError(f"Missing Buffer configuration: {', '.join(missing)}")
        post_input = {
            "text": _post_text(post),
            "channelId": settings.BUFFER_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNow",
            "needsApproval": False,
            "saveToDraft": False,
            "aiAssisted": True,
            "assets": [],
        }
        if post.image_url:
            post_input["assets"] = [{"image": {"url": post.image_url}}]
        data = _graphql(self.CREATE_POST, {"input": post_input})
        result = data.get("data", {}).get("createPost") or {}
        if result.get("message"):
            raise PublisherResponseError(f"Buffer rejected the post: {result['message']}")
        buffer_post = result.get("post") or {}
        if not buffer_post.get("id"):
            raise PublisherResponseError("Buffer accepted the request without returning a post ID.")
        return PublishResult(str(buffer_post["id"]), "SUBMITTED", data)

    def status(self, external_id):
        data = _graphql(self.GET_POST, {"input": {"id": external_id}})
        buffer_post = data.get("data", {}).get("post") or {}
        return str(buffer_post.get("status", "")).lower(), data


class WebhookPublisher:
    def __init__(self, url, secret, provider_name, target="ORGANIZATION"):
        self.url = url
        self.secret = secret
        self.provider_name = provider_name
        self.target = target

    def publish(self, post):
        if not self.url:
            raise PublisherConfigurationError(f"{self.provider_name} webhook URL is not configured.")
        payload = {
            "event": "linkedin.post.due",
            "idempotency_key": str(post.id),
            "page_name": post.settings.page_name,
            "target": self.target,
            "text": _post_text(post),
            "image_url": post.image_url,
            "image_prompt": post.image_prompt,
            "alt_text": post.alt_text,
            "scheduled_for": post.scheduled_for.isoformat(),
            "callback_url": f"{settings.PUBLIC_BACKEND_URL}/api/v3/linkedin/publisher-callback/",
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Idempotency-Key": str(post.id)}
        if self.secret:
            signature = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Nomad-Signature"] = signature
            headers["X-Nomad-Secret"] = self.secret
        response = requests.post(
            self.url,
            data=body,
            headers=headers,
            timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {}
        external_id = str(data.get("post_id") or data.get("id") or post.id)
        remote_state = str(data.get("status") or data.get("state") or "published").lower()
        state = "SUBMITTED" if remote_state in {"accepted", "queued", "scheduled", "submitted"} else "PUBLISHED"
        return PublishResult(external_id, state, data)


class LinkedInPublisher:
    def publish(self, post):
        mode = post.settings.publisher
        if mode == post.settings.MANUAL:
            raise PublisherConfigurationError("Manual handoff is selected; the post is ready to copy into LinkedIn.")
        if mode == post.settings.BUFFER:
            return BufferPublisher().publish(post)
        if mode == post.settings.N8N:
            target = settings.N8N_LINKEDIN_TARGET if settings.N8N_LINKEDIN_TARGET in {"PERSON", "ORGANIZATION"} else "PERSON"
            return WebhookPublisher(
                settings.N8N_LINKEDIN_WEBHOOK_URL,
                settings.N8N_LINKEDIN_WEBHOOK_SECRET,
                "n8n",
                target,
            ).publish(post)
        return WebhookPublisher(
            settings.LINKEDIN_PUBLISH_WEBHOOK_URL,
            settings.LINKEDIN_PUBLISH_WEBHOOK_SECRET,
            "Legacy provider",
        ).publish(post)
