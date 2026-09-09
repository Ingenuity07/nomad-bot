import json
import re
from difflib import SequenceMatcher

from django.conf import settings
from django.utils import timezone

from integrations.social.media import media_validation_issues
from integrations.social.models import (
    BrandProfile,
    ConnectionState,
    ContentSource,
    ContentSourceProcessingState,
    SocialNetwork,
    SocialPostState,
    SocialPostVariant,
    SocialWorkspaceSettings,
)
from integrations.social.publishing.registry import publishing_provider_registry
from integrations.social.publishing.types import ProviderName
from llm.router import IntelligentRouter


REPORT_VERSION = 1
COPY_LIMITS = {
    SocialNetwork.LINKEDIN: 3000,
    SocialNetwork.X: 280,
    SocialNetwork.INSTAGRAM: 2200,
}
HASHTAG_LIMITS = {
    SocialNetwork.LINKEDIN: 30,
    SocialNetwork.X: 10,
    SocialNetwork.INSTAGRAM: 30,
}
NETWORK_LABELS = {
    SocialNetwork.LINKEDIN: "LinkedIn",
    SocialNetwork.X: "X",
    SocialNetwork.INSTAGRAM: "Instagram",
}
GENERIC_HOOKS = (
    "in today's fast-paced world",
    "are you ready to",
    "game changer",
    "here's the thing",
    "unlock your potential",
    "we are thrilled to announce",
    "exciting news",
    "did you know",
)
PROMOTIONAL_TERMS = (
    "buy now", "book a call", "limited time", "don't miss", "act now",
    "best-in-class", "industry-leading", "revolutionary", "guaranteed",
)
SENSITIVE_PATTERNS = (
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "a possible payment-card number"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "a possible government identifier"),
    (re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|password)\s*[:=]\s*\S+", re.I), "a possible credential"),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "an email address"),
)


def _item(key, label, status, message, *, blocking=False):
    return {
        "key": key,
        "label": label,
        "status": status,
        "message": message,
        "blocking": bool(blocking),
    }


def _normalized(value):
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", str(value).lower()).split())


def _brand_snapshot(variant):
    if variant.post.brand_profile_version_id:
        return dict(variant.post.brand_profile_version.snapshot or {})
    workspace_settings = SocialWorkspaceSettings.objects.filter(workspace=variant.post.workspace).first()
    profile = BrandProfile.objects.filter(settings=workspace_settings).first() if workspace_settings else None
    if not profile:
        return {}
    return {
        "voice": profile.voice,
        "voice_rules": list(profile.voice_rules),
        "forbidden_topics": list(profile.forbidden_topics),
        "example_posts": list(profile.example_posts),
    }


def _platform_check(variant, copy, hashtags, metadata, media_snapshot):
    network = SocialNetwork(variant.network)
    label = NETWORK_LABELS[network]
    issues = []
    text_length = len(copy.strip())
    if network == SocialNetwork.X and metadata.get("format") != "THREAD":
        text_length += sum(len(str(tag)) + 1 for tag in hashtags)
    if not copy.strip():
        issues.append("Write the post before approval.")
    if text_length > COPY_LIMITS[network]:
        issues.append(f"{label} posts must be {COPY_LIMITS[network]:,} characters or fewer.")
    if len(hashtags) > HASHTAG_LIMITS[network]:
        issues.append(f"Use no more than {HASHTAG_LIMITS[network]} hashtags for {label}.")
    if metadata.get("format") == "THREAD":
        segments = metadata.get("thread") or []
        if network != SocialNetwork.X:
            issues.append("Threads are available for X posts only.")
        elif len(segments) < 2 or any(len(str(segment)) > 280 for segment in segments):
            issues.append("An X thread needs at least two posts, each 280 characters or fewer.")
    if metadata.get("format") == "CAROUSEL":
        slides = metadata.get("carousel_slides") or []
        if network != SocialNetwork.INSTAGRAM:
            issues.append("Carousels are available for Instagram posts only.")
        elif not 2 <= len(slides) <= 10:
            issues.append("An Instagram carousel needs between 2 and 10 slides.")
    capabilities = None
    if variant.connection_id:
        try:
            capabilities = publishing_provider_registry.create(ProviderName(variant.connection.provider)).capabilities
        except Exception:
            capabilities = None
    issues.extend(media_validation_issues(network, media_snapshot, capabilities))
    if issues:
        return _item("platform_fit", "Platform length and media", "BLOCKED", " ".join(dict.fromkeys(issues)), blocking=True)
    return _item("platform_fit", "Platform length and media", "PASS", f"Fits the current {label} limits.")


def _authorization_check(variant):
    if not variant.connection_id or variant.connection.status != ConnectionState.CONNECTED:
        return _item(
            "authorization", "Account authorization", "BLOCKED",
            f"Reconnect {NETWORK_LABELS[SocialNetwork(variant.network)]} before approval.", blocking=True,
        )
    return _item("authorization", "Account authorization", "PASS", "The social account is connected.")


def _prohibited_topics_check(copy, snapshot):
    forbidden = [str(value).strip() for value in snapshot.get("forbidden_topics", []) if str(value).strip()]
    normalized_copy = _normalized(copy)
    padded_copy = f" {normalized_copy} "
    matched = [
        topic for topic in forbidden
        if _normalized(topic) and f" {_normalized(topic)} " in padded_copy
    ]
    if matched:
        return _item(
            "prohibited_topics", "Prohibited topics", "BLOCKED",
            f"This includes a topic prohibited by the workspace: {', '.join(matched)}.", blocking=True,
        )
    return _item("prohibited_topics", "Prohibited topics", "PASS", "No configured prohibited topic was found.")


def _missing_alt_text_check(media_snapshot):
    missing = [
        index + 1 for index, item in enumerate(media_snapshot)
        if str(item.get("asset_type")) in {"IMAGE", "MULTI_IMAGE"}
        and not str(item.get("alt_text") or "").strip()
    ]
    if missing:
        return _item(
            "missing_alt_text", "Alt text", "REVIEW_SUGGESTED",
            f"Add alt text to image{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} for accessibility.",
        )
    return _item("missing_alt_text", "Alt text", "PASS", "All images include alt text.")


def _sensitive_data_check(copy):
    findings = [label for pattern, label in SENSITIVE_PATTERNS if pattern.search(copy)]
    if findings:
        return _item(
            "sensitive_data", "Sensitive data", "REVIEW_SUGGESTED",
            f"Review before sharing: the post may contain {', '.join(findings)}.",
        )
    return _item("sensitive_data", "Sensitive data", "PASS", "No common sensitive-data pattern was found.")


def _similarity_check(variant, copy):
    normalized_copy = _normalized(copy)
    if not normalized_copy:
        return _item("recent_similarity", "Similarity to recent posts", "PASS", "There is no copy to compare yet.")
    recent = SocialPostVariant.objects.filter(
        post__workspace=variant.post.workspace,
        status__in=[SocialPostState.SCHEDULED, SocialPostState.SUBMITTED, SocialPostState.PUBLISHED],
    ).exclude(pk=variant.pk).order_by("-updated_at").values_list("copy", flat=True)[:20]
    best = max((SequenceMatcher(None, normalized_copy, _normalized(value)).ratio() for value in recent), default=0)
    if best >= 0.82:
        return _item(
            "recent_similarity", "Similarity to recent posts", "REVIEW_SUGGESTED",
            f"This is {round(best * 100)}% similar to a recent workspace post. Consider a fresher angle.",
        )
    return _item("recent_similarity", "Similarity to recent posts", "PASS", "It is sufficiently distinct from recent posts.")


def _hook_check(variant, copy):
    hook = next((line.strip() for line in copy.splitlines() if line.strip()), "")
    generic = next((phrase for phrase in GENERIC_HOOKS if phrase in hook.lower()), "")
    recent_hooks = SocialPostVariant.objects.filter(post__workspace=variant.post.workspace).exclude(pk=variant.pk).order_by("-updated_at").values_list("copy", flat=True)[:20]
    repeated = bool(hook and any(_normalized(str(value).splitlines()[0] if str(value).splitlines() else "") == _normalized(hook) for value in recent_hooks))
    if generic or repeated:
        reason = "The opening repeats a recent hook." if repeated else f'The opening uses the common phrase “{generic}”.'
        return _item("hook_quality", "Hook originality", "REVIEW_SUGGESTED", reason)
    return _item("hook_quality", "Hook originality", "PASS", "The opening does not match common or recent hooks.")


def _promotion_check(copy):
    normalized = copy.lower()
    matches = [term for term in PROMOTIONAL_TERMS if term in normalized]
    emphatic = copy.count("!") >= 3 or sum(1 for word in copy.split() if len(word) > 3 and word.isupper()) >= 3
    if len(matches) >= 2 or emphatic:
        return _item(
            "promotional_intensity", "Promotional intensity", "REVIEW_SUGGESTED",
            "The wording may feel strongly promotional. Confirm that this matches the intended goal.",
        )
    return _item("promotional_intensity", "Promotional intensity", "PASS", "The promotion level looks measured.")


class LLMQualitySuggestionAnalyzer:
    """Advisory-only semantic checks. Its output can never create a hard blocker."""

    system_prompt = (
        "You are a cautious social-content reviewer. Return strict JSON only. "
        "Do not rewrite the content. Flag uncertainty rather than asserting it as fact."
    )

    def __init__(self, router=None):
        self.router = router or IntelligentRouter()

    def analyze(self, *, copy, hashtags, brand_snapshot, source_text):
        prompt = f"""
Review this social post without rewriting it.
Copy: {copy}
Hashtags: {json.dumps(hashtags)}
Voice: {brand_snapshot.get('voice', '')}
Voice rules: {json.dumps(brand_snapshot.get('voice_rules', []))}
Example posts: {json.dumps(brand_snapshot.get('example_posts', []))}
Available supporting sources: {source_text or '[none]'}

Return exactly:
{{
  "voice_match": {{"review": false, "explanation": "brief reason"}},
  "unsupported_claims": {{"review": false, "explanation": "brief reason"}},
  "copyright_attribution": {{"review": false, "explanation": "brief reason"}}
}}
Only suggest review for a concrete concern. Treat all findings as advisory.
""".strip()
        result = self.router.generate(prompt=prompt, system_prompt=self.system_prompt)
        raw = str(result.get("text") or "") if result.get("type") == "text" else ""
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I).strip()
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}


def _source_text(variant, metadata):
    references = metadata.get("source_references") if isinstance(metadata, dict) else []
    source_ids = [item.get("id") for item in references or [] if isinstance(item, dict) and item.get("id")]
    sources = ContentSource.objects.filter(
        workspace=variant.post.workspace,
        pk__in=source_ids,
        is_active=True,
        processing_status=ContentSourceProcessingState.READY,
    ).exclude(extracted_text="")
    return "\n\n".join(f"[{source.label}] {source.extracted_text[:2000]}" for source in sources)


def _fallback_suggestions(copy, snapshot, source_text):
    has_voice_rules = bool(snapshot.get("voice") or snapshot.get("voice_rules"))
    factual_signal = bool(re.search(r"\b\d+(?:\.\d+)?%|\baccording to\b|\bstudies? (?:show|found)\b|\bresearch (?:shows|found)\b", copy, re.I))
    attribution_signal = bool(re.search(r'[“”"]|\b(?:quote|excerpt|adapted from|via)\b', copy, re.I))
    unavailable = "Automated language review was unavailable. Review this item before approval."
    return {
        "voice_match": {"review": has_voice_rules, "explanation": unavailable if has_voice_rules else "No specific voice concern was detected."},
        "unsupported_claims": {"review": factual_signal and not source_text, "explanation": unavailable if factual_signal and not source_text else "No unsupported factual-claim signal was detected."},
        "copyright_attribution": {"review": attribution_signal, "explanation": unavailable if attribution_signal else "No obvious quotation or attribution concern was detected."},
    }


def _advisory_items(variant, copy, hashtags, metadata, snapshot, analyzer):
    source_text = _source_text(variant, metadata)
    raw = None
    if analyzer is not None:
        try:
            raw = analyzer.analyze(
                copy=copy,
                hashtags=hashtags,
                brand_snapshot=snapshot,
                source_text=source_text,
            )
        except Exception:
            raw = None
    if raw is None:
        raw = _fallback_suggestions(copy, snapshot, source_text)
    definitions = (
        ("voice_match", "Voice match"),
        ("unsupported_claims", "Unsupported factual claims"),
        ("copyright_attribution", "Copyright and attribution"),
    )
    items = []
    for key, label in definitions:
        value = raw.get(key) if isinstance(raw, dict) else {}
        value = value if isinstance(value, dict) else {}
        review = value.get("review") is True
        message = str(value.get("explanation") or ("Review suggested." if review else "No concern was detected."))[:500]
        items.append(_item(key, label, "REVIEW_SUGGESTED" if review else "PASS", message))
    return items


def build_quality_report(*, variant, copy, hashtags, metadata, media_snapshot, analyzer=None):
    """Build a report for exact snapshot values without changing user-authored content."""
    snapshot = _brand_snapshot(variant)
    deterministic = [
        _platform_check(variant, copy, hashtags, metadata, media_snapshot),
        _authorization_check(variant),
        _prohibited_topics_check(copy, snapshot),
        _similarity_check(variant, copy),
        _hook_check(variant, copy),
        _promotion_check(copy),
        _missing_alt_text_check(media_snapshot),
        _sensitive_data_check(copy),
    ]
    if analyzer is None and getattr(settings, "SOCIAL_QUALITY_LLM_ENABLED", False):
        analyzer = LLMQualitySuggestionAnalyzer()
    suggestions = _advisory_items(variant, copy, hashtags, metadata, snapshot, analyzer)
    all_items = deterministic + suggestions
    counts = {
        "passed": sum(item["status"] == "PASS" for item in all_items),
        "review": sum(item["status"] == "REVIEW_SUGGESTED" for item in all_items),
        "blocked": sum(item["status"] == "BLOCKED" for item in all_items),
    }
    return {
        "schema_version": REPORT_VERSION,
        "checked_at": timezone.now().isoformat(),
        "hard_blocked": counts["blocked"] > 0,
        "review_suggested": counts["review"] > 0,
        "summary": counts,
        "deterministic": deterministic,
        "suggestions": suggestions,
    }


def hard_block_messages(report):
    if not isinstance(report, dict):
        return []
    return [
        str(item.get("message") or "This version needs a required change.")
        for item in report.get("deterministic", [])
        if isinstance(item, dict) and item.get("blocking") and item.get("status") == "BLOCKED"
    ]
