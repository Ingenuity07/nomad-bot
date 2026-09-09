import ipaddress
import re
from datetime import timedelta
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from integrations.social.models import (
    BrandProfile,
    BrandProfileVersion,
    ContentSource,
    ContentSourceProcessingState,
    ContentSourceType,
    SocialWorkspaceSettings,
    StoryInterview,
    StoryInterviewState,
    VoiceRuleSuggestion,
    VoiceRuleSuggestionState,
)


BRAND_FIELDS = (
    "business_description", "audience", "goals", "voice", "voice_rules", "example_posts",
    "content_pillars", "calls_to_action", "visual_direction", "forbidden_topics", "performance_rules",
)
LIST_FIELDS = {"goals", "voice_rules", "example_posts", "content_pillars", "calls_to_action", "forbidden_topics", "performance_rules"}
BASE_STORY_QUESTIONS = [
    {"id": "moment", "label": "What happened this week that customers or your team could learn from?"},
    {"id": "why", "label": "Why did that moment matter?"},
    {"id": "lesson", "label": "What practical lesson would you share?"},
]


def _clean_text(value, *, limit=100000):
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    return value.strip()[:limit]


def _clean_list(value, *, item_limit=2000):
    if not isinstance(value, list):
        raise ValidationError("Use a list of short entries.")
    return [_clean_text(item, limit=item_limit) for item in value if _clean_text(item, limit=item_limit)][:100]


def brand_snapshot(profile):
    return {field: list(getattr(profile, field)) if field in LIST_FIELDS else getattr(profile, field) for field in BRAND_FIELDS}


def brand_brain_for(workspace):
    settings, _ = SocialWorkspaceSettings.objects.get_or_create(workspace=workspace)
    profile, _ = BrandProfile.objects.get_or_create(settings=settings)
    if not profile.versions.exists():
        BrandProfileVersion.objects.create(brand_profile=profile, version=1, snapshot=brand_snapshot(profile))
    return profile


@transaction.atomic
def update_brand_brain(profile, values, *, user):
    changed = False
    for field in BRAND_FIELDS:
        if field not in values:
            continue
        value = _clean_list(values[field]) if field in LIST_FIELDS else _clean_text(values[field], limit=10000)
        if getattr(profile, field) != value:
            setattr(profile, field, value)
            changed = True
    if changed:
        profile.full_clean()
        profile.save(update_fields=[*BRAND_FIELDS, "updated_at"])
        latest = profile.versions.order_by("-version").first()
        BrandProfileVersion.objects.create(
            brand_profile=profile,
            version=(latest.version + 1) if latest else 1,
            snapshot=brand_snapshot(profile),
            created_by=user,
        )
    return profile


def _validate_public_url(value):
    value = _clean_text(value, limit=2000)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError({"source_url": "Enter a public http or https URL without embedded credentials."})
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local") or hostname.endswith(".internal"):
        raise ValidationError({"source_url": "This URL is not publicly accessible."})
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValidationError({"source_url": "This URL is not publicly accessible."})
    return value


@transaction.atomic
def create_content_source(*, workspace, owner, values):
    try:
        source_type = ContentSourceType(str(values.get("source_type") or "TEXT").upper())
    except ValueError as exc:
        raise ValidationError({"source_type": "Choose text, URL, PDF, transcript, or voice-note transcript."}) from exc
    label = _clean_text(values.get("label"), limit=255)
    supplied_text = _clean_text(values.get("text_content"))
    source_url = ""
    original_filename = _clean_text(values.get("original_filename"), limit=255)
    metadata = values.get("metadata") if isinstance(values.get("metadata"), dict) else {}
    if source_type == ContentSourceType.URL:
        source_url = _validate_public_url(values.get("source_url"))
        status = ContentSourceProcessingState.PENDING
        extracted_text = ""
    elif source_type in {ContentSourceType.PDF, ContentSourceType.DOCUMENT}:
        if not original_filename:
            raise ValidationError({"original_filename": "Add the document filename."})
        status = ContentSourceProcessingState.PENDING
        extracted_text = ""
    else:
        if not supplied_text:
            raise ValidationError({"text_content": "Add the source text or transcript."})
        status = ContentSourceProcessingState.READY
        extracted_text = supplied_text
    return ContentSource.objects.create(
        workspace=workspace,
        owner=owner,
        source_type=source_type,
        label=label or original_filename or source_url[:255] or "Untitled source",
        text_content=supplied_text if status == ContentSourceProcessingState.READY else "",
        extracted_text=extracted_text,
        source_url=source_url,
        original_filename=original_filename,
        processing_status=status,
        metadata=metadata,
    )


def complete_source_processing(source, *, extracted_text="", error=""):
    """Trusted processor boundary; browser clients cannot supply extracted URL/PDF text."""
    safe_text = _clean_text(extracted_text)
    if safe_text:
        source.extracted_text = safe_text
        source.processing_status = ContentSourceProcessingState.READY
        source.processing_error = ""
    else:
        source.extracted_text = ""
        source.processing_status = ContentSourceProcessingState.FAILED
        source.processing_error = _clean_text(error, limit=2000) or "No usable text was found."
    source.save(update_fields=["extracted_text", "processing_status", "processing_error", "updated_at"])
    return source


def record_voice_edit(*, workspace, before, after):
    before = str(before or "").strip()
    after = str(after or "").strip()
    if not before or not after or before == after:
        return None
    if len(after) > len(before) * 0.75:
        return None
    profile = brand_brain_for(workspace)
    suggestion, _ = VoiceRuleSuggestion.objects.get_or_create(
        brand_profile=profile,
        signal_key="prefer_concise_copy",
        defaults={"suggested_rule": "Prefer concise posts and remove repetition.", "evidence": []},
    )
    if suggestion.status != VoiceRuleSuggestionState.PENDING:
        return suggestion
    evidence = list(suggestion.evidence)
    evidence.append({"before_length": len(before), "after_length": len(after), "recorded_at": timezone.now().isoformat()})
    suggestion.evidence = evidence[-10:]
    suggestion.evidence_count = len(evidence)
    suggestion.save(update_fields=["evidence", "evidence_count", "updated_at"])
    return suggestion


@transaction.atomic
def decide_voice_suggestion(suggestion, *, confirm, user):
    if suggestion.status != VoiceRuleSuggestionState.PENDING:
        raise ValidationError({"detail": "This suggestion has already been decided."})
    if confirm:
        profile = suggestion.brand_profile
        rules = list(profile.voice_rules)
        if suggestion.suggested_rule not in rules:
            update_brand_brain(profile, {"voice_rules": [*rules, suggestion.suggested_rule]}, user=user)
        suggestion.status = VoiceRuleSuggestionState.CONFIRMED
        suggestion.confirmed_by = user
        suggestion.confirmed_at = timezone.now()
    else:
        suggestion.status = VoiceRuleSuggestionState.DISMISSED
    suggestion.save(update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"])
    return suggestion


def current_story_interview(workspace, *, owner):
    today = timezone.localdate()
    week_of = today - timedelta(days=today.weekday())
    interview, _ = StoryInterview.objects.get_or_create(
        workspace=workspace,
        week_of=week_of,
        defaults={"owner": owner, "questions": BASE_STORY_QUESTIONS},
    )
    return interview


def save_story_answers(interview, answers):
    if interview.status == StoryInterviewState.APPROVED:
        raise ValidationError({"detail": "This week’s story is already approved."})
    if not isinstance(answers, dict):
        raise ValidationError({"answers": "Answer one or more interview questions."})
    clean = dict(interview.answers)
    clean.update({str(key): _clean_text(value, limit=5000) for key, value in answers.items()})
    questions = list(interview.questions)
    if clean.get("moment") and "customer" in clean["moment"].lower() and not any(item.get("id") == "customer_voice" for item in questions):
        questions.append({"id": "customer_voice", "label": "What did the customer say or do that made this memorable?"})
    interview.answers = clean
    interview.questions = questions[:4]
    interview.save(update_fields=["answers", "questions", "updated_at"])
    return interview


@transaction.atomic
def approve_story(interview, *, user):
    if interview.status == StoryInterviewState.APPROVED:
        return interview
    answered = [(item["label"], interview.answers.get(item["id"], "")) for item in interview.questions]
    answered = [(question, answer) for question, answer in answered if answer]
    if len(answered) < 2:
        raise ValidationError({"answers": "Answer at least two questions before saving this story."})
    text = "\n\n".join(f"{question}\n{answer}" for question, answer in answered)
    source = ContentSource.objects.create(
        workspace=interview.workspace,
        owner=user,
        source_type=ContentSourceType.TRANSCRIPT,
        label=f"Weekly story — {interview.week_of.isoformat()}",
        text_content=text,
        extracted_text=text,
        processing_status=ContentSourceProcessingState.READY,
        metadata={"story_interview_id": str(interview.id), "week_of": interview.week_of.isoformat()},
    )
    interview.approved_source = source
    interview.status = StoryInterviewState.APPROVED
    interview.approved_at = timezone.now()
    interview.save(update_fields=["approved_source", "status", "approved_at", "updated_at"])
    return interview
