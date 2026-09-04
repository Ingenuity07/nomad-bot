import json
import re
from dataclasses import dataclass

from llm.router import IntelligentRouter

from ..models import ContentBrief, LinkedInAutomationSettings


@dataclass(frozen=True)
class GeneratedPostContent:
    topic: str
    hook: str
    body: str
    hashtags: list[str]
    image_prompt: str
    alt_text: str
    provider: str = "fallback"
    model: str = "template"


class LinkedInContentGenerator:
    """Turns reusable brand context into a complete LinkedIn post package."""

    system_prompt = (
        "You are a senior B2B LinkedIn editor. Return one original post as strict JSON only. "
        "Never invent customer results, statistics, partnerships, or product capabilities. "
        "Keep the post useful, human, scannable, and below 2,700 characters."
    )

    def __init__(self, router=None):
        self.router = router or IntelligentRouter()

    def generate(self, settings: LinkedInAutomationSettings, brief: ContentBrief, sequence: int = 1) -> GeneratedPostContent:
        prompt = self._prompt(settings, brief, sequence)
        result = self.router.generate(prompt=prompt, system_prompt=self.system_prompt)
        if result.get("type") == "text":
            parsed = self._parse_json(result.get("text", ""))
            if parsed:
                return GeneratedPostContent(
                    topic=str(parsed.get("topic") or brief.label or "Operational insight")[:255],
                    hook=str(parsed.get("hook") or "")[:500],
                    body=str(parsed.get("body") or "").strip()[:2700],
                    hashtags=self._hashtags(parsed.get("hashtags")),
                    image_prompt=str(parsed.get("image_prompt") or settings.image_style).strip(),
                    alt_text=str(parsed.get("alt_text") or "Branded illustration for the LinkedIn post")[:500],
                    provider=str(result.get("provider") or "router"),
                    model=str(result.get("model") or "routed"),
                )
        return self._fallback(settings, brief, sequence)

    def _prompt(self, settings, brief, sequence):
        return f"""
Create post variation {sequence} for the LinkedIn Page {settings.page_name}.

Company: {settings.company_description}
Audience: {settings.audience}
Voice: {settings.brand_voice}
Language: {settings.language}
Content pillars: {json.dumps(settings.content_pillars)}
Allowed calls to action: {json.dumps(settings.calls_to_action)}
Never discuss: {json.dumps(settings.forbidden_topics)}
Reusable source context: {brief.context}
Image direction: {settings.image_style}

Return exactly this JSON shape:
{{
  "topic": "short internal title",
  "hook": "strong first line",
  "body": "complete post with short paragraphs; do not append hashtags",
  "hashtags": ["#Three", "#To", "#FiveTags"],
  "image_prompt": "standalone prompt for a 4:5 professional branded image with no text or logos",
  "alt_text": "accessible description of the planned image"
}}
""".strip()

    @staticmethod
    def _parse_json(value):
        cleaned = re.sub(r"^```(?:json)?|```$", "", value.strip(), flags=re.IGNORECASE).strip()
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) and parsed.get("body") else None
        except (TypeError, json.JSONDecodeError):
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not match:
                return None
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) and parsed.get("body") else None
            except json.JSONDecodeError:
                return None

    @staticmethod
    def _hashtags(value):
        if not isinstance(value, list):
            value = []
        tags = []
        for item in value[:5]:
            tag = re.sub(r"[^A-Za-z0-9_]", "", str(item).lstrip("#"))
            if tag:
                tags.append(f"#{tag}")
        return tags or ["#Logistics", "#RoutePlanning", "#Operations"]

    def _fallback(self, settings, brief, sequence):
        pillar = settings.content_pillars[(sequence - 1) % len(settings.content_pillars)] if settings.content_pillars else "better daily operations"
        hook = f"A small shift in {pillar.lower()} can remove hours of avoidable work."
        body = (
            f"{hook}\n\n"
            f"Here is the idea we keep coming back to at {settings.page_name}: "
            f"{brief.context.strip()}\n\n"
            "The useful question is not whether a process works on a quiet day. It is whether the team can repeat it when plans change, volume rises, and time is tight.\n\n"
            "Make the next step visible, measure the friction, then improve one handoff at a time."
        )
        return GeneratedPostContent(
            topic=(brief.label or pillar.title())[:255],
            hook=hook,
            body=body[:2700],
            hashtags=["#Logistics", "#RoutePlanning", "#Operations"],
            image_prompt=f"{settings.image_style}. Visual concept about {pillar}. 4:5 portrait, no text, no logos.",
            alt_text=f"Editorial illustration representing {pillar}.",
        )

