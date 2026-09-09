import json
import re
from datetime import timedelta

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from integrations.social.media import media_validation_issues
from integrations.social.models import (
    BrandProfile,
    ConnectionState,
    ContentSource,
    ContentSourceProcessingState,
    SocialConnection,
    SocialNetwork,
    SocialPost,
    SocialPostState,
    SocialPostVariant,
    SocialPostSource,
    SocialWorkspaceSettings,
)
from integrations.social.services.lifecycle import create_version, edit_variant, transition_variant
from llm.router import IntelligentRouter


NETWORK_LABELS = {
    SocialNetwork.LINKEDIN: "LinkedIn",
    SocialNetwork.X: "X",
    SocialNetwork.INSTAGRAM: "Instagram",
}
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
CONTROL_OPTIONS = {
    "tone": {"Professional", "Friendly", "Bold", "Educational"},
    "goal": {"Awareness", "Engagement", "Education", "Leads"},
    "length": {"Short", "Medium", "Long"},
}
REWRITE_ACTIONS = {
    "MAKE_SHORTER",
    "MAKE_PERSONAL",
    "NEW_HOOK",
    "REDUCE_PROMOTION",
    "CREATE_X_THREAD",
    "CREATE_INSTAGRAM_CAROUSEL",
}


class ComposerValidationError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__("The post needs changes before it can be sent for approval.")


def connected_networks(workspace):
    connections = (
        SocialConnection.objects.filter(workspace=workspace, status=ConnectionState.CONNECTED)
        .order_by("network", "-updated_at")
    )
    selected = {}
    for connection in connections:
        selected.setdefault(connection.network, connection)
    return selected


def normalize_networks(workspace, values):
    if not isinstance(values, list) or not values:
        raise ValidationError({"networks": "Choose at least one connected social account."})
    connections = connected_networks(workspace)
    normalized = []
    for value in values:
        try:
            network = SocialNetwork(str(value).upper())
        except ValueError as exc:
            raise ValidationError({"networks": "Choose a supported social network."}) from exc
        draft_only_allowed = bool(getattr(django_settings, "CONTENT_STUDIO_DRAFT_ONLY_ALLOWED", False))
        if network not in connections and not (draft_only_allowed and network == SocialNetwork.LINKEDIN):
            raise ValidationError({"networks": f"Connect {NETWORK_LABELS[network]} before creating its post."})
        if network not in normalized:
            normalized.append(network)
    return normalized, connections


def normalize_controls(values):
    values = values if isinstance(values, dict) else {}
    result = {
        "tone": str(values.get("tone") or "Professional").title(),
        "goal": str(values.get("goal") or "Awareness").title(),
        "length": str(values.get("length") or "Medium").title(),
        "include_image": bool(values.get("include_image", False)),
    }
    errors = {}
    for field, allowed in CONTROL_OPTIONS.items():
        if result[field] not in allowed:
            errors[field] = f"Choose one of: {', '.join(sorted(allowed))}."
    if errors:
        raise ValidationError(errors)
    return result


def _scheduled_time():
    return timezone.now() + timedelta(days=1)


def _selected_sources(source=None, sources=None):
    values = list(sources or ([] if source is None else [source]))
    return list({item.id: item for item in values}.values())


def _sync_post_sources(post, sources):
    SocialPostSource.objects.filter(post=post).delete()
    SocialPostSource.objects.bulk_create([
        SocialPostSource(post=post, source=source, sort_order=index)
        for index, source in enumerate(sources)
    ])
    post.source = sources[0] if sources else None
    post.save(update_fields=["source", "updated_at"])


def create_draft(*, workspace, idea_title, idea_text="", source=None, sources=None, networks, controls=None):
    networks, connections = normalize_networks(workspace, networks)
    controls = normalize_controls(controls)
    selected_sources = _selected_sources(source, sources)
    title = str(idea_title or "").strip()
    text = str(idea_text or "").strip()
    if not title and not text and not selected_sources:
        raise ValidationError({"idea_text": "Describe the post idea or choose a saved source."})
    if any(item.workspace_id != workspace.id for item in selected_sources):
        raise ValidationError({"source_ids": "Choose sources from this workspace."})
    with transaction.atomic():
        post = SocialPost.objects.create(
            workspace=workspace,
            source=selected_sources[0] if selected_sources else None,
            idea_title=(title or (selected_sources[0].label if selected_sources else "Untitled idea"))[:255],
            idea_text=text,
            state=SocialPostState.DRAFT,
            metadata={"generation_controls": controls},
        )
        _sync_post_sources(post, selected_sources)
        for network in networks:
            SocialPostVariant.objects.create(
                post=post,
                connection=connections.get(network),
                network=network,
                copy="",
                hashtags=[],
                scheduled_for=_scheduled_time(),
                status=SocialPostState.DRAFT,
            )
    return post


def update_post(*, post, idea_title=None, idea_text=None, source=None, sources=None, source_was_supplied=False, controls=None):
    fields = []
    if idea_title is not None:
        post.idea_title = str(idea_title).strip()[:255] or "Untitled idea"
        fields.append("idea_title")
    if idea_text is not None:
        post.idea_text = str(idea_text).strip()
        fields.append("idea_text")
    if source_was_supplied:
        selected_sources = _selected_sources(source, sources)
        if any(item.workspace_id != post.workspace_id for item in selected_sources):
            raise ValidationError({"source_ids": "Choose sources from this workspace."})
        _sync_post_sources(post, selected_sources)
    if controls is not None:
        metadata = dict(post.metadata)
        metadata["generation_controls"] = normalize_controls(controls)
        post.metadata = metadata
        fields.append("metadata")
    if fields:
        post.save(update_fields=[*fields, "updated_at"])
    return post


@transaction.atomic
def sync_draft_networks(*, post, networks):
    networks, connections = normalize_networks(post.workspace, networks)
    omitted = post.variants.exclude(network__in=networks)
    protected = omitted.exclude(status=SocialPostState.DRAFT).exists() or omitted.filter(publish_jobs__isnull=False).exists()
    if protected:
        raise ValidationError({"networks": "A platform version already in review or publishing cannot be removed."})
    omitted.delete()
    for network in networks:
        SocialPostVariant.objects.get_or_create(
            post=post,
            network=network,
            defaults={
                "connection": connections.get(network),
                "copy": "",
                "hashtags": [],
                "scheduled_for": _scheduled_time(),
                "status": SocialPostState.DRAFT,
            },
        )
    return networks, connections


class SocialContentGenerator:
    system_prompt = (
        "You are a social editor. Return strict JSON only. Write a genuinely different draft for each "
        "requested network, respecting its conventions and limits. Never invent claims or results."
    )

    def __init__(self, router=None):
        self.router = router or IntelligentRouter()

    def generate(self, *, post, networks, controls):
        settings = SocialWorkspaceSettings.objects.filter(workspace=post.workspace).first()
        brand = BrandProfile.objects.filter(settings=settings).first() if settings else None
        sources = [reference.source for reference in post.source_references.select_related("source")]
        unavailable = [source.label for source in sources if source.processing_status != ContentSourceProcessingState.READY or not source.extracted_text]
        if unavailable:
            raise ValidationError({"source_ids": f"These sources are not ready yet: {', '.join(unavailable)}."})
        source_text = self._source_context(sources)
        prompt = self._prompt(post, networks, controls, settings, brand, source_text)
        try:
            result = self.router.generate(prompt=prompt, system_prompt=self.system_prompt)
            parsed = self._parse(result.get("text", "")) if result.get("type") == "text" else {}
        except Exception:
            parsed = {}
        generated = {}
        used_copy = set()
        for network in networks:
            value = parsed.get(network) if isinstance(parsed, dict) else None
            item = self._normalize_item(value, network, controls)
            if not item["copy"] or item["copy"] in used_copy:
                item = self._fallback(post, network, controls, settings, source_text)
            used_copy.add(item["copy"])
            generated[network] = item
        return generated

    @staticmethod
    def _source_context(sources):
        return "\n\n".join(f"[{source.label}]\n{source.extracted_text}" for source in sources if source.extracted_text)

    @staticmethod
    def _prompt(post, networks, controls, settings, brand, source_text):
        limits = {network: COPY_LIMITS[network] for network in networks}
        return f"""
Create platform-native social drafts for {', '.join(networks)}.
Idea title: {post.idea_title}
Idea: {post.idea_text}
Saved source: {source_text}
Brand: {settings.brand_name if settings else ''}
Business description: {brand.business_description if brand else ''}
Audience: {brand.audience if brand else ''}
Voice: {brand.voice if brand else ''}
Voice rules: {json.dumps(brand.voice_rules if brand else [])}
Business goals: {json.dumps(brand.goals if brand else [])}
Content pillars: {json.dumps(brand.content_pillars if brand else [])}
Calls to action: {json.dumps(brand.calls_to_action if brand else [])}
Accepted performance rules: {json.dumps(brand.performance_rules if brand else [])}
Example posts: {json.dumps(brand.example_posts if brand else [])}
Visual direction: {brand.visual_direction if brand else ''}
Prohibited topics: {json.dumps(brand.forbidden_topics if brand else [])}
Tone: {controls['tone']}
Goal: {controls['goal']}
Length: {controls['length']}
Character limits: {json.dumps(limits)}

Return an object keyed by the uppercase network name. Each value must have:
{{"copy":"complete post without hashtags","hashtags":["#Tag"],"image_prompt":"specific visual direction","alt_text":"accessible description"}}
""".strip()

    @staticmethod
    def _parse(value):
        cleaned = re.sub(r"^```(?:json)?|```$", "", str(value).strip(), flags=re.I).strip()
        try:
            parsed = json.loads(cleaned)
        except (TypeError, json.JSONDecodeError):
            match = re.search(r"\{.*\}", cleaned, flags=re.S)
            if not match:
                return {}
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return {str(key).upper(): val for key, val in parsed.items()} if isinstance(parsed, dict) else {}

    @staticmethod
    def _hashtags(values):
        if not isinstance(values, list):
            return []
        tags = []
        for value in values:
            clean = re.sub(r"[^A-Za-z0-9_]", "", str(value).lstrip("#"))
            if clean and f"#{clean}" not in tags:
                tags.append(f"#{clean}")
        return tags

    def _normalize_item(self, value, network, controls):
        value = value if isinstance(value, dict) else {}
        hashtags = self._hashtags(value.get("hashtags"))[: HASHTAG_LIMITS[network]]
        copy_limit = COPY_LIMITS[network]
        if network == SocialNetwork.X:
            copy_limit -= sum(len(tag) + 1 for tag in hashtags)
        copy = str(value.get("copy") or "").strip()[:copy_limit]
        return {
            "copy": copy,
            "hashtags": hashtags,
            "metadata": {
                "image_prompt": str(value.get("image_prompt") or "").strip(),
                "alt_text": str(value.get("alt_text") or "").strip()[:500],
                "include_image": controls["include_image"],
            },
        }

    def _fallback(self, post, network, controls, settings, source_text):
        brand = settings.brand_name if settings else "our team"
        idea = post.idea_text or source_text or post.idea_title
        goal_line = {
            "Awareness": "Here is the idea worth noticing.",
            "Engagement": "What would you change?",
            "Education": "Here is a practical way to apply it.",
            "Leads": "If this is a priority for your team, let’s compare notes.",
        }[controls["goal"]]
        if network == SocialNetwork.X:
            copy = f"{post.idea_title}: {idea}\n\n{goal_line}"
        elif network == SocialNetwork.INSTAGRAM:
            copy = f"A closer look at {post.idea_title.lower()} ✨\n\n{idea}\n\n{goal_line}"
        else:
            copy = f"{post.idea_title}\n\n{idea}\n\nAt {brand}, we believe the useful next step is the one a team can repeat.\n\n{goal_line}"
        targets = {"Short": 180, "Medium": 700, "Long": COPY_LIMITS[network]}
        tags = {
            SocialNetwork.LINKEDIN: ["#Business", "#Leadership"],
            SocialNetwork.X: ["#Business"],
            SocialNetwork.INSTAGRAM: ["#BusinessTips", "#BehindTheIdea", "#Growth"],
        }[network]
        copy_limit = COPY_LIMITS[network]
        if network == SocialNetwork.X:
            copy_limit -= sum(len(tag) + 1 for tag in tags)
        copy = copy[:min(targets[controls["length"]], copy_limit)]
        return {
            "copy": copy,
            "hashtags": tags,
            "metadata": {
                "image_prompt": f"Editorial social image about {post.idea_title}, no text or logos",
                "alt_text": f"Editorial visual about {post.idea_title}",
                "include_image": controls["include_image"],
            },
        }


@transaction.atomic
def generate_variants(*, post, networks, controls, generator=None):
    from integrations.social.services.knowledge import brand_brain_for

    networks, connections = sync_draft_networks(post=post, networks=networks)
    controls = normalize_controls(controls)
    update_post(post=post, controls=controls)
    brand = brand_brain_for(post.workspace)
    brand_version = brand.versions.order_by("-version").first()
    references = list(post.source_references.select_related("source"))
    unavailable = [reference.source.label for reference in references if reference.source.processing_status != ContentSourceProcessingState.READY or not reference.source.extracted_text]
    if unavailable:
        raise ValidationError({"source_ids": f"These sources are not ready yet: {', '.join(unavailable)}."})
    post.brand_profile_version = brand_version
    post.save(update_fields=["brand_profile_version", "updated_at"])
    SocialPostSource.objects.filter(pk__in=[reference.pk for reference in references]).update(used_in_generation=True)
    source_references = [
        {"id": str(reference.source_id), "label": reference.source.label, "source_type": reference.source.source_type}
        for reference in references
    ]
    generated = (generator or SocialContentGenerator()).generate(
        post=post,
        networks=networks,
        controls=controls,
    )
    for network in networks:
        value = generated[network]
        value["metadata"] = {
            **value["metadata"],
            "brand_brain_version": {"id": str(brand_version.id), "version": brand_version.version},
            "source_references": source_references,
        }
        variant, created = SocialPostVariant.objects.get_or_create(
            post=post,
            network=network,
            defaults={
                "connection": connections.get(network),
                "copy": value["copy"],
                "hashtags": value["hashtags"],
                "metadata": value["metadata"],
                "scheduled_for": _scheduled_time(),
                "status": SocialPostState.DRAFT,
            },
        )
        if not created:
            variant.connection = connections.get(network)
            variant.save(update_fields=["connection", "updated_at"])
            edit_variant(
                variant,
                copy=value["copy"],
                hashtags=value["hashtags"],
                metadata=value["metadata"],
            )
        else:
            create_version(variant)
    post.refresh_from_db()
    return post


def variant_validation(variant):
    fields = {"copy": [], "hashtags": [], "media": [], "connection": []}
    copy = variant.copy.strip()
    if not copy:
        fields["copy"].append("Write the post before sending it for approval.")
    total_copy = len(copy)
    if variant.network == SocialNetwork.X and variant.metadata.get("format") != "THREAD":
        total_copy += sum(len(tag) + 1 for tag in variant.hashtags)
    if total_copy > COPY_LIMITS[variant.network]:
        fields["copy"].append(
            f"{NETWORK_LABELS[variant.network]} posts must be {COPY_LIMITS[variant.network]:,} characters or fewer."
        )
    if len(variant.hashtags) > HASHTAG_LIMITS[variant.network]:
        fields["hashtags"].append(
            f"Use no more than {HASHTAG_LIMITS[variant.network]} hashtags for {NETWORK_LABELS[variant.network]}."
        )
    if not variant.connection or variant.connection.status != ConnectionState.CONNECTED:
        fields["connection"].append(f"Reconnect {NETWORK_LABELS[variant.network]} before publishing.")
    fields["media"].extend(media_validation_issues(variant.network, variant.media_assets.all()))
    if variant.metadata.get("format") == "THREAD":
        segments = variant.metadata.get("thread") or []
        if variant.network != SocialNetwork.X:
            fields["copy"].append("Threads are available for X posts only.")
        elif len(segments) < 2 or any(len(str(segment)) > 280 for segment in segments):
            fields["copy"].append("An X thread needs at least two posts, each 280 characters or fewer.")
    if variant.metadata.get("format") == "CAROUSEL":
        slides = variant.metadata.get("carousel_slides") or []
        if variant.network != SocialNetwork.INSTAGRAM:
            fields["copy"].append("Carousels are available for Instagram posts only.")
        elif not 2 <= len(slides) <= 10:
            fields["copy"].append("An Instagram carousel needs between 2 and 10 slides.")
    fields = {field: messages for field, messages in fields.items() if messages}
    return {"valid": not fields, "fields": fields}


def _sentences(value):
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", value) if item.strip()]


@transaction.atomic
def rewrite_variant(*, variant, action):
    action = str(action).upper()
    if action not in REWRITE_ACTIONS:
        raise ValidationError({"action": "Choose a supported writing action."})
    copy = variant.copy.strip()
    metadata = dict(variant.metadata)
    if action == "MAKE_SHORTER":
        sentences = _sentences(copy)
        copy = " ".join(sentences[: max(1, (len(sentences) + 1) // 2)])[:max(80, len(copy) // 2)]
    elif action == "MAKE_PERSONAL":
        copy = f"Here’s what I’ve learned:\n\n{copy}" if not copy.lower().startswith(("i ", "i’", "my ")) else copy
    elif action == "NEW_HOOK":
        rest = "\n".join(copy.splitlines()[1:]).strip()
        copy = f"What if the usual approach is the thing slowing us down?\n\n{rest or copy}"
    elif action == "REDUCE_PROMOTION":
        copy = re.sub(r"(?i)\b(book a call|contact us|buy now|sign up|our solution|our product)\b[^.!?]*[.!?]?", "", copy)
        copy = re.sub(r"\n{3,}", "\n\n", copy).strip()
    elif action == "CREATE_X_THREAD":
        if variant.network != SocialNetwork.X:
            raise ValidationError({"action": "Create X thread is available on the X tab."})
        chunks = _sentences(copy) or [copy]
        midpoint = max(1, len(chunks) // 2)
        thread = [" ".join(chunks[:midpoint])[:276], " ".join(chunks[midpoint:])[:276]]
        if not thread[1]:
            thread[1] = "What would you add?"
        metadata.update({"format": "THREAD", "thread": thread})
        copy = thread[0]
    else:
        if variant.network != SocialNetwork.INSTAGRAM:
            raise ValidationError({"action": "Create Instagram carousel is available on the Instagram tab."})
        sentences = _sentences(copy) or [copy]
        slides = [variant.post.idea_title, *sentences[:8]]
        if len(slides) < 2:
            slides.append("Save this idea for later.")
        metadata.update({"format": "CAROUSEL", "carousel_slides": slides[:10]})
    return edit_variant(variant, copy=copy, metadata=metadata)


@transaction.atomic
def save_variant_draft(*, variant, copy=None, hashtags=None, scheduled_for=None):
    variant = SocialPostVariant.objects.select_for_update().select_related("post").get(pk=variant.pk)
    if variant.status == SocialPostState.DRAFT:
        fields = []
        if copy is not None and str(copy) != variant.copy:
            variant.copy = str(copy)
            fields.append("copy")
        if hashtags is not None and list(hashtags) != list(variant.hashtags):
            variant.hashtags = list(hashtags)
            fields.append("hashtags")
        if scheduled_for is not None and scheduled_for != variant.scheduled_for:
            variant.scheduled_for = scheduled_for
            fields.append("scheduled_for")
        if fields:
            variant.save(update_fields=[*fields, "updated_at"])
        return variant
    return edit_variant(
        variant,
        copy=str(copy) if copy is not None else None,
        hashtags=hashtags,
        scheduled_for=scheduled_for,
    )


@transaction.atomic
def submit_for_review(post):
    variants = list(post.variants.select_related("connection").prefetch_related("media_assets"))
    if not variants:
        raise ValidationError({"variants": "Generate at least one platform post first."})
    errors = {str(variant.id): variant_validation(variant) for variant in variants}
    errors = {key: value for key, value in errors.items() if not value["valid"]}
    if errors:
        raise ComposerValidationError({"variants": errors})
    for variant in variants:
        metadata = dict(variant.metadata)
        metadata["review_state"] = "NEEDS_REVIEW"
        metadata.pop("review_note", None)
        variant.metadata = metadata
        variant.save(update_fields=["metadata", "updated_at"])
        create_version(variant)
        if variant.status == SocialPostState.DRAFT:
            transition_variant(variant, SocialPostState.NEEDS_REVIEW)
    post.refresh_from_db()
    return post


def source_for_workspace(workspace, source_id):
    if not source_id:
        return None
    try:
        return ContentSource.objects.filter(workspace=workspace, pk=source_id, is_active=True).first()
    except (ValidationError, ValueError):
        return None


def sources_for_workspace(workspace, source_ids):
    if source_ids is None:
        return []
    if not isinstance(source_ids, list) or len(source_ids) > 20:
        return None
    clean_ids = list(dict.fromkeys(str(value) for value in source_ids if value))
    try:
        sources = list(ContentSource.objects.filter(workspace=workspace, pk__in=clean_ids, is_active=True))
    except (ValidationError, ValueError):
        return None
    by_id = {str(source.id): source for source in sources}
    return [by_id[value] for value in clean_ids] if len(by_id) == len(clean_ids) else None
