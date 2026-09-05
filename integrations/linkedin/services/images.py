import base64

import requests
from django.conf import settings


GEMINI_ASPECT_RATIOS = {
    "4:5": "ASPECT_RATIO_FOUR_BY_FIVE",
}
GEMINI_IMAGE_SIZES = {
    "512": "IMAGE_SIZE_FIVE_TWELVE",
    "1K": "IMAGE_SIZE_ONE_K",
    "2K": "IMAGE_SIZE_TWO_K",
    "4K": "IMAGE_SIZE_FOUR_K",
}


def _raise_provider_error(response, provider):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            message = response.json().get("error", {}).get("message", "")
        except (TypeError, ValueError):
            message = ""
        if response.status_code == 429:
            raise RuntimeError(
                f"{provider} image quota is unavailable. Enable billing or increase the image-model quota, then try again."
            ) from exc
        detail = f": {message}" if message else ""
        raise RuntimeError(f"{provider} image request failed ({response.status_code}){detail}") from exc


def image_provider_status():
    if not settings.LINKEDIN_GENERATE_IMAGES:
        return {"ready": False, "label": "Image generation is off", "detail": "Set LINKEDIN_GENERATE_IMAGES=True"}
    provider = settings.LINKEDIN_IMAGE_PROVIDER
    if provider == "gemini":
        ready, label, missing = bool(settings.GEMINI_API_KEY), "Gemini image", "GEMINI_API_KEY"
    elif provider == "openai":
        ready, label, missing = bool(settings.OPENAI_API_KEY), "OpenAI image", "OPENAI_API_KEY"
    else:
        ready = bool(settings.GEMINI_API_KEY or settings.OPENAI_API_KEY)
        label = "Gemini image" if settings.GEMINI_API_KEY else "OpenAI image" if settings.OPENAI_API_KEY else "Automatic image provider"
        missing = "GEMINI_API_KEY or OPENAI_API_KEY"
    return {"ready": ready, "label": label, "detail": "Ready for 4:5 post images" if ready else f"Add {missing}"}


class LinkedInImageGenerator:
    """Generate feed-ready 4:5 artwork with Gemini first and OpenAI fallback."""

    openai_endpoint = "https://api.openai.com/v1/images/generations"
    gemini_endpoint = "https://generativelanguage.googleapis.com/v1/models/{model}:generateContent"

    @staticmethod
    def art_direct(prompt):
        return f"""
Create one premium editorial image for a LinkedIn Company Page post.

CORE VISUAL IDEA
{prompt.strip()}

ART DIRECTION
- Use a single clear focal concept that communicates the idea in under two seconds.
- Compose specifically for a 4:5 portrait feed canvas with generous breathing room and safe margins.
- Make the subject concrete and relevant; avoid generic office teams, handshakes, floating UI screens, and random charts.
- Use restrained, intentional colors, realistic materials and lighting, and a polished campaign-quality finish.
- Keep the background simple enough that the image remains legible on a phone.
- No words, captions, letters, numbers, logos, watermarks, interface chrome, borders, or split-screen collage.
- Do not invent product screenshots, customer identities, statistics, awards, or brand marks.

Return only the final image.
""".strip()

    def generate(self, post_id, prompt):
        if not settings.LINKEDIN_GENERATE_IMAGES:
            return "", {"status": "not_configured"}, b""
        directed_prompt = self.art_direct(prompt)
        provider = settings.LINKEDIN_IMAGE_PROVIDER
        errors = []
        if provider in {"auto", "gemini"} and settings.GEMINI_API_KEY:
            try:
                return self._generate_gemini(post_id, directed_prompt)
            except Exception as exc:
                if provider == "gemini":
                    raise
                errors.append(f"Gemini: {exc}")
        if provider in {"auto", "openai"} and settings.OPENAI_API_KEY:
            try:
                return self._generate_openai(post_id, directed_prompt)
            except Exception as exc:
                if provider == "openai":
                    raise
                errors.append(f"OpenAI: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return "", {"status": "not_configured", "detail": "Add GEMINI_API_KEY or OPENAI_API_KEY"}, b""

    def _generate_gemini(self, post_id, prompt):
        response = requests.post(
            self.gemini_endpoint.format(model=settings.GEMINI_IMAGE_MODEL),
            headers={"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "responseFormat": {"image": {
                        "aspectRatio": GEMINI_ASPECT_RATIOS["4:5"],
                        "imageSize": GEMINI_IMAGE_SIZES.get(settings.GEMINI_IMAGE_SIZE, "IMAGE_SIZE_ONE_K"),
                    }},
                },
            },
            timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS,
        )
        _raise_provider_error(response, "Gemini")
        data = response.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        image_part = next((item.get("inlineData") or item.get("inline_data") for item in parts if item.get("inlineData") or item.get("inline_data")), None)
        if not image_part or not image_part.get("data"):
            raise RuntimeError("Gemini returned no image data.")
        raw_bytes = base64.b64decode(image_part["data"])
        content_type = image_part.get("mimeType") or image_part.get("mime_type") or "image/png"
        return self._result(post_id, raw_bytes, "gemini", settings.GEMINI_IMAGE_MODEL, content_type)

    def _generate_openai(self, post_id, prompt):
        response = requests.post(
            self.openai_endpoint,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": settings.OPENAI_IMAGE_MODEL,
                "prompt": prompt,
                "size": "1024x1536",
                "quality": settings.OPENAI_IMAGE_QUALITY,
                "output_format": "png",
            },
            timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS,
        )
        _raise_provider_error(response, "OpenAI")
        item = response.json()["data"][0]
        if item.get("url"):
            image_response = requests.get(item["url"], timeout=settings.LINKEDIN_HTTP_TIMEOUT_SECONDS)
            _raise_provider_error(image_response, "OpenAI image download")
            raw_bytes = image_response.content
        else:
            raw = item.get("b64_json")
            if not raw:
                raise RuntimeError("OpenAI returned no image data.")
            raw_bytes = base64.b64decode(raw)
        return self._result(post_id, raw_bytes, "openai", settings.OPENAI_IMAGE_MODEL, "image/png")

    @staticmethod
    def _result(post_id, raw_bytes, provider, model, content_type):
        url = f"{settings.PUBLIC_BACKEND_URL}/api/v3/linkedin/posts/{post_id}/image/"
        metadata = {
            "status": "generated",
            "provider": provider,
            "model": model,
            "content_type": content_type,
            "bytes": len(raw_bytes),
            "aspect_ratio": "4:5",
        }
        return url, metadata, raw_bytes


# Backwards-compatible import for existing callers and tests.
OpenAIImageGenerator = LinkedInImageGenerator
