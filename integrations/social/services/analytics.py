import hashlib
import json
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from integrations.social.models import (
    AnalyticsSuggestion,
    AnalyticsSuggestionState,
    ConnectionState,
    MediaAssetType,
    PublishJob,
    PublishJobState,
    SocialMetricName,
    SocialMetricObservation,
    SocialNetwork,
    SocialConnection,
    SocialPostState,
    SocialPostVariant,
)
from integrations.social.publishing.registry import publishing_provider_registry
from integrations.social.publishing.types import (
    AccountMetricsResult,
    GetAccountMetricsRequest,
    GetPostMetricsRequest,
    PostMetricsResult,
    ProviderName,
    PublishingNetwork,
)
from integrations.social.services.knowledge import brand_brain_for, update_brand_brain


METRIC_NAMES = tuple(SocialMetricName.values)
METRIC_LABELS = dict(SocialMetricName.choices)
ENGAGEMENT_METRICS = {
    SocialMetricName.REACTIONS,
    SocialMetricName.LIKES,
    SocialMetricName.COMMENTS,
    SocialMetricName.SHARES,
    SocialMetricName.REPOSTS,
}
MIN_SUGGESTION_POSTS = 6
MIN_SEGMENT_POSTS = 2


def _ingestion_key(*, workspace_id, variant_id, connection_id, provider, observation):
    canonical = json.dumps({
        "workspace": str(workspace_id),
        "variant": str(variant_id or ""),
        "connection": str(connection_id or ""),
        "provider": str(provider),
        "metric": observation.metric_name.value,
        "value": observation.value,
        "measured_at": observation.measured_at.isoformat(),
        "reference": dict(observation.raw_reference),
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@transaction.atomic
def ingest_metric_result(*, job, result: PostMetricsResult):
    """Application-owned persistence boundary for provider-normalized observations."""
    job = PublishJob.objects.select_for_update().select_related("variant__post", "connection").get(pk=job.pk)
    if ProviderName(job.provider) != ProviderName(result.provider):
        raise ValidationError("Metric results must come from the provider stored on the publish job.")
    created = []
    for observation in result.observations:
        try:
            metric_name = SocialMetricName(observation.metric_name.value)
        except ValueError as exc:
            raise ValidationError("The provider returned an unsupported metric name.") from exc
        key = _ingestion_key(
            workspace_id=job.variant.post.workspace_id,
            variant_id=job.variant_id,
            connection_id=job.connection_id,
            provider=job.provider,
            observation=observation,
        )
        metric, was_created = SocialMetricObservation.objects.get_or_create(
            ingestion_key=key,
            defaults={
                "workspace_id": job.variant.post.workspace_id,
                "variant_id": job.variant_id,
                "connection_id": job.connection_id,
                "publish_job_id": job.id,
                "provider": job.provider,
                "metric_name": metric_name,
                "value": observation.value,
                "measured_at": observation.measured_at,
                "raw_reference": dict(observation.raw_reference),
            },
        )
        if was_created:
            created.append(metric)
    return tuple(created)


@transaction.atomic
def ingest_account_metric_result(*, connection, result: AccountMetricsResult):
    """Persist account-level values without attributing them to individual posts."""
    connection = SocialConnection.objects.select_for_update().get(pk=connection.pk)
    if ProviderName(connection.provider) != ProviderName(result.provider):
        raise ValidationError("Metric results must come from the provider stored on the social connection.")
    created = []
    for observation in result.observations:
        metric_name = SocialMetricName(observation.metric_name.value)
        if metric_name != SocialMetricName.FOLLOWER_GROWTH:
            raise ValidationError("Only follower growth is supported as an account-level metric.")
        key = _ingestion_key(
            workspace_id=connection.workspace_id,
            variant_id=None,
            connection_id=connection.id,
            provider=connection.provider,
            observation=observation,
        )
        metric, was_created = SocialMetricObservation.objects.get_or_create(
            ingestion_key=key,
            defaults={
                "workspace_id": connection.workspace_id,
                "variant_id": None,
                "connection_id": connection.id,
                "publish_job_id": None,
                "provider": connection.provider,
                "metric_name": metric_name,
                "value": observation.value,
                "measured_at": observation.measured_at,
                "raw_reference": dict(observation.raw_reference),
            },
        )
        if was_created:
            created.append(metric)
    return tuple(created)


def refresh_published_metrics(*, limit=100):
    jobs = (
        PublishJob.objects.filter(status=PublishJobState.PUBLISHED)
        .exclude(external_id="")
        .select_related("variant__post", "connection")
        .order_by("-completed_at")[:limit]
    )
    counts = {"checked": 0, "connections_checked": 0, "observations": 0, "failed": 0}
    for job in jobs:
        try:
            adapter = publishing_provider_registry.create(ProviderName(job.provider))
            result = adapter.get_post_metrics(GetPostMetricsRequest(
                workspace_id=job.variant.post.workspace_id,
                external_id=job.external_id,
                provider_profile_id=job.connection.provider_profile_id if job.connection else "",
                provider_account_id=job.connection.provider_account_id if job.connection else "",
            ))
            counts["checked"] += 1
            counts["observations"] += len(ingest_metric_result(job=job, result=result))
        except Exception:
            counts["failed"] += 1
    connections = (
        SocialConnection.objects.filter(
            status=ConnectionState.CONNECTED,
            provider__in=[provider.value for provider in ProviderName],
        ).order_by("-updated_at")[:limit]
    )
    for connection in connections:
        try:
            adapter = publishing_provider_registry.create(ProviderName(connection.provider))
            result = adapter.get_account_metrics(GetAccountMetricsRequest(
                workspace_id=connection.workspace_id,
                network=PublishingNetwork(connection.network),
                provider_profile_id=connection.provider_profile_id,
                provider_account_id=connection.provider_account_id,
            ))
            counts["connections_checked"] += 1
            counts["observations"] += len(ingest_account_metric_result(connection=connection, result=result))
        except Exception:
            counts["failed"] += 1
    return counts


def _latest_observations(workspace):
    observations = SocialMetricObservation.objects.filter(workspace=workspace).order_by(
        "-measured_at", "-created_at"
    )
    latest = {}
    for observation in observations:
        subject = str(observation.variant_id or f"connection:{observation.connection_id}")
        latest.setdefault((subject, observation.metric_name), observation)
    return latest


def _metric_cells(subjects, latest, *, follower_subjects=()):
    cells = {}
    for metric_name in METRIC_NAMES:
        metric_subjects = follower_subjects if metric_name == SocialMetricName.FOLLOWER_GROWTH else subjects
        values = [
            latest[(str(subject), metric_name)].value
            for subject in metric_subjects
            if (str(subject), metric_name) in latest
        ]
        cells[metric_name] = {
            "label": METRIC_LABELS[metric_name],
            "available": bool(values),
            "value": sum(values) if values else None,
            "measured_posts": len(values),
        }
    return cells


def _format_for(variant):
    explicit = str(variant.metadata.get("format") or "").upper()
    if explicit in {"THREAD", "CAROUSEL"}:
        return explicit.replace("_", " ").title()
    asset_types = list(variant.media_assets.values_list("asset_type", flat=True))
    if len(asset_types) > 1 and all(value == MediaAssetType.IMAGE for value in asset_types):
        return "Multiple images"
    if asset_types:
        return dict(MediaAssetType.choices).get(asset_types[0], asset_types[0].title())
    return "Text"


def _pillar_for(variant, pillars):
    value = str(variant.metadata.get("content_pillar") or variant.post.metadata.get("content_pillar") or "").strip()
    if value:
        return value
    haystack = f"{variant.post.idea_title} {variant.copy}".lower()
    return next((pillar for pillar in pillars if pillar.lower() in haystack), "Unassigned")


def _comparison_rows(variants, latest, dimension, pillars):
    groups = defaultdict(list)
    for variant in variants:
        key = {
            "platform": variant.get_network_display(),
            "topic": variant.post.idea_title or "Untitled",
            "content_pillar": _pillar_for(variant, pillars),
            "format": _format_for(variant),
        }[dimension]
        groups[key].append(variant.id)
    if dimension == "platform" and not groups:
        for network in SocialNetwork:
            groups[network.label] = []
    return [
        {"key": label, "label": label, "posts": len(ids), "metrics": _metric_cells(ids, latest)}
        for label, ids in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))[:12]
    ]


def _variant_rates(variants, latest):
    rates = {}
    for variant in variants:
        subject = str(variant.id)
        impression = latest.get((subject, SocialMetricName.IMPRESSIONS))
        view = latest.get((subject, SocialMetricName.VIEWS))
        denominator = impression.value if impression else view.value if view else 0
        engagement_values = []
        reaction = latest.get((subject, SocialMetricName.REACTIONS))
        like = latest.get((subject, SocialMetricName.LIKES))
        if reaction or like:
            engagement_values.append((reaction or like).value)
        for name in (SocialMetricName.COMMENTS, SocialMetricName.SHARES, SocialMetricName.REPOSTS):
            item = latest.get((subject, name))
            if item:
                engagement_values.append(item.value)
        if denominator > 0 and engagement_values:
            rates[variant.id] = sum(engagement_values) / denominator * 100
    return rates


def _suggestion_candidates(variants, latest, pillars):
    rates = _variant_rates(variants, latest)
    if len(rates) < MIN_SUGGESTION_POSTS:
        return []
    candidates = []
    for dimension in ("platform", "content_pillar", "format"):
        groups = defaultdict(list)
        for variant in variants:
            if variant.id not in rates:
                continue
            segment = {
                "platform": variant.get_network_display(),
                "content_pillar": _pillar_for(variant, pillars),
                "format": _format_for(variant),
            }[dimension]
            if segment != "Unassigned":
                groups[segment].append(rates[variant.id])
        qualified = {key: values for key, values in groups.items() if len(values) >= MIN_SEGMENT_POSTS}
        if len(qualified) < 2:
            continue
        averages = {key: sum(values) / len(values) for key, values in qualified.items()}
        best = max(averages, key=averages.get)
        comparison_values = [value for key, value in averages.items() if key != best]
        comparison = sum(comparison_values) / len(comparison_values)
        if averages[best] <= comparison * 1.1:
            continue
        dimension_label = {"platform": "platform", "content_pillar": "content pillar", "format": "format"}[dimension]
        rule = f"Test more {best} content when it fits the idea, and compare the results with other {dimension_label}s."
        candidates.append({
            "dimension": dimension,
            "segment": best,
            "rule": rule,
            "rationale": (
                f"{best} was associated with a {averages[best]:.2f}% average interaction rate "
                f"across {len(qualified[best])} posts, compared with {comparison:.2f}% for other {dimension_label}s. "
                "This is an observed association, not evidence that the format caused the difference."
            ),
            "evidence": {
                "posts": len(qualified[best]),
                "average_interaction_rate": round(averages[best], 3),
                "comparison_interaction_rate": round(comparison, 3),
                "denominator": "impressions when available, otherwise views",
                "disclaimer": "This comparison shows correlation only and does not establish causation.",
            },
        })
    return candidates[:3]


def refresh_suggestions(workspace, variants, latest, pillars):
    for candidate in _suggestion_candidates(variants, latest, pillars):
        fingerprint = hashlib.sha256(
            f"{candidate['dimension']}:{candidate['segment']}:{candidate['rule']}".encode()
        ).hexdigest()
        AnalyticsSuggestion.objects.get_or_create(
            workspace=workspace,
            fingerprint=fingerprint,
            defaults={
                "dimension": candidate["dimension"],
                "segment": candidate["segment"],
                "suggested_rule": candidate["rule"],
                "rationale": candidate["rationale"],
                "evidence": candidate["evidence"],
            },
        )


def _serialize_suggestion(item):
    return {
        "id": str(item.id),
        "dimension": item.dimension,
        "segment": item.segment,
        "rule": item.suggested_rule,
        "rationale": item.rationale,
        "evidence": item.evidence,
        "status": item.status,
    }


def analytics_dashboard(workspace):
    profile = brand_brain_for(workspace)
    variants = list(
        SocialPostVariant.objects.filter(
            post__workspace=workspace,
            status=SocialPostState.PUBLISHED,
        ).select_related("post").prefetch_related("media_assets")
    )
    latest = _latest_observations(workspace)
    refresh_suggestions(workspace, variants, latest, list(profile.content_pillars))
    variant_ids = [variant.id for variant in variants]
    follower_subjects = [
        f"connection:{connection_id}"
        for connection_id in SocialConnection.objects.filter(workspace=workspace).values_list("id", flat=True)
    ]
    suggestions = AnalyticsSuggestion.objects.filter(workspace=workspace).order_by("status", "-created_at")[:20]
    return {
        "summary": _metric_cells(variant_ids, latest, follower_subjects=follower_subjects),
        "comparisons": {
            dimension: _comparison_rows(variants, latest, dimension, list(profile.content_pillars))
            for dimension in ("platform", "topic", "content_pillar", "format")
        },
        "suggestions": [_serialize_suggestion(item) for item in suggestions],
        "data_note": (
            f"Suggestions require at least {MIN_SUGGESTION_POSTS} posts with impressions or views and interaction data. "
            "Comparisons show association only; they do not prove what caused a result."
        ),
    }


@transaction.atomic
def decide_analytics_suggestion(suggestion, *, accept, user):
    suggestion = AnalyticsSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if suggestion.status != AnalyticsSuggestionState.PENDING:
        raise ValidationError({"detail": "This suggestion has already been decided."})
    if accept:
        profile = brand_brain_for(suggestion.workspace)
        rules = list(profile.performance_rules)
        if suggestion.suggested_rule not in rules:
            update_brand_brain(
                profile,
                {"performance_rules": [*rules, suggestion.suggested_rule]},
                user=user,
            )
        suggestion.status = AnalyticsSuggestionState.ACCEPTED
        suggestion.applied_brand_version = profile.versions.order_by("-version").first()
    else:
        suggestion.status = AnalyticsSuggestionState.DISMISSED
    suggestion.decided_by = user
    suggestion.decided_at = timezone.now()
    suggestion.save(update_fields=["status", "decided_by", "decided_at", "applied_brand_version", "updated_at"])
    return suggestion
