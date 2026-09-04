import base64

import requests
from django.conf import settings

class OpenAIImageGenerator:
    """Optional image adapter. The automation remains usable when it is not configured."""

    endpoint = "https://api.openai.com/v1/images/generations"

    def generate(self, post_id, prompt):
        if not settings.OPENAI_API_KEY or not settings.LINKEDIN_GENERATE_IMAGES:
            return "", {"status": "not_configured"}, b""
        response = requests.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": settings.OPENAI_IMAGE_MODEL,
                "prompt": prompt,
                "size": "1024x1536",
                "quality": "medium",
                "output_format": "png",
            },
            timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        item = response.json()["data"][0]
        if item.get("url"):
            image_response = requests.get(item["url"], timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS)
            image_response.raise_for_status()
            raw_bytes = image_response.content
        else:
            raw = item.get("b64_json")
            if not raw:
                return "", {"status": "empty_response"}, b""
            raw_bytes = base64.b64decode(raw)
        url = f"{settings.PUBLIC_BACKEND_URL}/api/v3/linkedin/posts/{post_id}/image/"
        return url, {"status": "generated", "model": settings.OPENAI_IMAGE_MODEL, "bytes": len(raw_bytes)}, raw_bytes
