from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID


class ProviderName(str, Enum):
    UPLOAD_POST = "UPLOAD_POST"
    ZERNIO = "ZERNIO"


class PublishingNetwork(str, Enum):
    LINKEDIN = "LINKEDIN"
    X = "X"
    INSTAGRAM = "INSTAGRAM"


class PublishingMediaType(str, Enum):
    IMAGE = "IMAGE"
    MULTI_IMAGE = "MULTI_IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"


class PublishingMetricName(str, Enum):
    IMPRESSIONS = "IMPRESSIONS"
    VIEWS = "VIEWS"
    REACTIONS = "REACTIONS"
    LIKES = "LIKES"
    COMMENTS = "COMMENTS"
    SHARES = "SHARES"
    REPOSTS = "REPOSTS"
    CLICKS = "CLICKS"
    FOLLOWER_GROWTH = "FOLLOWER_GROWTH"


class PublishOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class RoutingOutcome(str, Enum):
    READY = "READY"
    CONNECTION_REQUIRED = "CONNECTION_REQUIRED"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"


class ProviderErrorCategory(str, Enum):
    CONFIGURATION = "CONFIGURATION"
    AUTHENTICATION = "AUTHENTICATION"
    VALIDATION = "VALIDATION"
    RATE_LIMIT = "RATE_LIMIT"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


def immutable_mapping(value: Mapping[str, Any] | None = None):
    return MappingProxyType(dict(value or {}))


SENSITIVE_KEY_PARTS = ("secret", "token", "password", "authorization", "cookie", "credential", "api_key")


def safe_metadata_mapping(value: Mapping[str, Any] | None = None):
    sanitized = {}
    for key, item in (value or {}).items():
        normalized_key = str(key).lower()
        if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
            sanitized[key] = "<redacted>"
        elif isinstance(item, Mapping):
            sanitized[key] = dict(safe_metadata_mapping(item))
        elif isinstance(item, (list, tuple)):
            sanitized[key] = tuple(
                dict(safe_metadata_mapping(entry)) if isinstance(entry, Mapping) else entry
                for entry in item
            )
        else:
            sanitized[key] = item
    return immutable_mapping(sanitized)


@dataclass(frozen=True)
class ProviderCredentials:
    values: Mapping[str, str] = field(repr=False, default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "values", immutable_mapping(self.values))

    def __repr__(self):
        return "ProviderCredentials(values=<redacted>)"


@dataclass(frozen=True)
class ProviderCapabilities:
    networks: frozenset[PublishingNetwork]
    media_types: frozenset[PublishingMediaType]
    supports_connection_completion: bool = False
    supports_account_listing: bool = True
    supports_cancellation: bool = False
    supports_status_polling: bool = True
    supports_webhooks: bool = True
    metric_names: frozenset[PublishingMetricName] = frozenset()


@dataclass(frozen=True)
class ProviderErrorInfo:
    category: ProviderErrorCategory
    message: str
    safe_details: Mapping[str, Any] = field(default_factory=dict)
    retry_after_seconds: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "safe_details", safe_metadata_mapping(self.safe_details))


@dataclass(frozen=True)
class SocialAccount:
    provider_profile_id: str
    provider_account_id: str
    network: PublishingNetwork
    display_name: str
    account_type: str
    capabilities: ProviderCapabilities


@dataclass(frozen=True)
class PostMedia:
    media_type: PublishingMediaType
    storage_url: str
    alt_text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metadata", immutable_mapping(self.metadata))


@dataclass(frozen=True)
class NormalizedPost:
    network: PublishingNetwork
    text: str
    hashtags: tuple[str, ...] = ()
    media: tuple[PostMedia, ...] = ()
    scheduled_for: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "hashtags", tuple(self.hashtags))
        object.__setattr__(self, "media", tuple(self.media))
        object.__setattr__(self, "metadata", immutable_mapping(self.metadata))


@dataclass(frozen=True)
class ConnectionUrlRequest:
    workspace_id: UUID
    redirect_uri: str
    state: str
    requested_networks: tuple[PublishingNetwork, ...] = ()


@dataclass(frozen=True)
class ConnectionUrlResult:
    provider: ProviderName
    url: str = field(repr=False)
    state: str
    expires_at: datetime | None = None

    def __repr__(self):
        return (
            f"ConnectionUrlResult(provider={self.provider!r}, url=<redacted>, "
            f"state={self.state!r}, expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True)
class CompleteConnectionRequest:
    workspace_id: UUID
    redirect_uri: str
    state: str
    authorization_code: str = field(repr=False)


@dataclass(frozen=True)
class CompleteConnectionResult:
    provider: ProviderName
    provider_connection_id: str
    connected: bool
    safe_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "safe_metadata", safe_metadata_mapping(self.safe_metadata))


@dataclass(frozen=True)
class ListSocialAccountsRequest:
    workspace_id: UUID
    provider_connection_id: str


@dataclass(frozen=True)
class ListSocialAccountsResult:
    provider: ProviderName
    accounts: tuple[SocialAccount, ...]

    def __post_init__(self):
        object.__setattr__(self, "accounts", tuple(self.accounts))


@dataclass(frozen=True)
class ValidatePostRequest:
    account: SocialAccount
    post: NormalizedPost


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    field: str = ""


@dataclass(frozen=True)
class ValidatePostResult:
    valid: bool
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class PublishNowRequest:
    workspace_id: UUID
    idempotency_key: str
    account: SocialAccount
    post: NormalizedPost
    callback_url: str = ""


@dataclass(frozen=True)
class PublishResult:
    provider: ProviderName
    outcome: PublishOutcome
    external_id: str = ""
    provider_status: str = ""
    error: ProviderErrorInfo | None = None
    checked_at: datetime | None = None
    safe_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "safe_metadata", safe_metadata_mapping(self.safe_metadata))


@dataclass(frozen=True)
class CancelPublishRequest:
    workspace_id: UUID
    external_id: str
    idempotency_key: str = ""


@dataclass(frozen=True)
class CancelPublishResult:
    provider: ProviderName
    cancelled: bool
    outcome: PublishOutcome
    provider_status: str = ""
    error: ProviderErrorInfo | None = None


@dataclass(frozen=True)
class GetPublishStatusRequest:
    workspace_id: UUID
    external_id: str
    idempotency_key: str = ""


@dataclass(frozen=True)
class GetPostMetricsRequest:
    workspace_id: UUID
    external_id: str
    provider_profile_id: str = ""
    provider_account_id: str = ""


@dataclass(frozen=True)
class GetAccountMetricsRequest:
    workspace_id: UUID
    network: PublishingNetwork
    provider_profile_id: str
    provider_account_id: str


@dataclass(frozen=True)
class NormalizedMetricObservation:
    metric_name: PublishingMetricName
    value: int
    measured_at: datetime
    raw_reference: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metric_name", PublishingMetricName(self.metric_name))
        object.__setattr__(self, "value", int(self.value))
        object.__setattr__(self, "raw_reference", safe_metadata_mapping(self.raw_reference))


@dataclass(frozen=True)
class PostMetricsResult:
    provider: ProviderName
    observations: tuple[NormalizedMetricObservation, ...] = ()
    unavailable_metrics: frozenset[PublishingMetricName] = frozenset()
    checked_at: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "unavailable_metrics", frozenset(self.unavailable_metrics))


@dataclass(frozen=True)
class AccountMetricsResult:
    provider: ProviderName
    observations: tuple[NormalizedMetricObservation, ...] = ()
    unavailable_metrics: frozenset[PublishingMetricName] = frozenset()
    checked_at: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "unavailable_metrics", frozenset(self.unavailable_metrics))


@dataclass(frozen=True)
class WebhookRequest:
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    received_at: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "headers", immutable_mapping(self.headers))

    def __repr__(self):
        return "WebhookRequest(headers=<redacted>, body=<redacted>, received_at=%r)" % self.received_at


@dataclass(frozen=True)
class NormalizedWebhookEvent:
    event_type: str
    outcome: PublishOutcome
    external_id: str = ""
    idempotency_key: str = ""
    occurred_at: datetime | None = None
    safe_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "safe_metadata", safe_metadata_mapping(self.safe_metadata))


@dataclass(frozen=True)
class WebhookParseResult:
    provider: ProviderName
    verified: bool
    events: tuple[NormalizedWebhookEvent, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "events", tuple(self.events))


@dataclass(frozen=True)
class HealthStatusRequest:
    workspace_id: UUID | None = None


@dataclass(frozen=True)
class HealthStatusResult:
    provider: ProviderName
    configured: bool
    healthy: bool
    capabilities: ProviderCapabilities
    detail: str = ""
    checked_at: datetime | None = None


@dataclass(frozen=True)
class PublishingRouteResult:
    provider: ProviderName
    outcome: RoutingOutcome
    connection_id: UUID | None = None
    publish_job_id: UUID | None = None
    detail: str = ""

    @property
    def ready(self):
        return self.outcome == RoutingOutcome.READY
