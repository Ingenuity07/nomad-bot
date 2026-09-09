"""Small transformations shared by provider adapters."""

from datetime import datetime, timezone

from .types import NormalizedMetricObservation, PublishingMetricName


METRIC_ALIASES = {
    "impressions": PublishingMetricName.IMPRESSIONS,
    "impression": PublishingMetricName.IMPRESSIONS,
    "views": PublishingMetricName.VIEWS,
    "view": PublishingMetricName.VIEWS,
    "reactions": PublishingMetricName.REACTIONS,
    "reaction": PublishingMetricName.REACTIONS,
    "likes": PublishingMetricName.LIKES,
    "like": PublishingMetricName.LIKES,
    "comments": PublishingMetricName.COMMENTS,
    "comment": PublishingMetricName.COMMENTS,
    "shares": PublishingMetricName.SHARES,
    "share": PublishingMetricName.SHARES,
    "reposts": PublishingMetricName.REPOSTS,
    "repost": PublishingMetricName.REPOSTS,
    "retweets": PublishingMetricName.REPOSTS,
    "clicks": PublishingMetricName.CLICKS,
    "click": PublishingMetricName.CLICKS,
    "follows": PublishingMetricName.FOLLOWER_GROWTH,
    "new_followers": PublishingMetricName.FOLLOWER_GROWTH,
    "followers_gained": PublishingMetricName.FOLLOWER_GROWTH,
    "follower_growth": PublishingMetricName.FOLLOWER_GROWTH,
}


def normalize_post_metrics(metrics, *, measured_at=None, raw_reference=None):
    """Normalize supported counters while preserving absent fields as unavailable."""
    measured_at = measured_at or datetime.now(timezone.utc)
    reference = dict(raw_reference or {})
    observations = []
    for provider_name, value in (metrics or {}).items():
        metric_name = METRIC_ALIASES.get(str(provider_name).strip().lower())
        if metric_name is None or isinstance(value, bool):
            continue
        try:
            numeric_value = int(value)
        except (TypeError, ValueError):
            continue
        if numeric_value < 0 and metric_name != PublishingMetricName.FOLLOWER_GROWTH:
            continue
        observations.append(NormalizedMetricObservation(
            metric_name=metric_name,
            value=numeric_value,
            measured_at=measured_at,
            raw_reference={**reference, "provider_metric": str(provider_name)},
        ))
    return tuple(observations)


def media_storage_urls(media_items):
    urls = []
    for media in media_items:
        if media.storage_url:
            urls.append(media.storage_url)
        extra_urls = media.metadata.get("storage_urls", ())
        if isinstance(extra_urls, (list, tuple)):
            urls.extend(str(url) for url in extra_urls if url)
    return tuple(dict.fromkeys(urls))


def normalized_post_text(post):
    parts = [post.text.strip(), " ".join(post.hashtags).strip()]
    return "\n\n".join(part for part in parts if part)


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
