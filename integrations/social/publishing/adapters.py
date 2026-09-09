from datetime import datetime, timezone

from .contract import PublishingProvider
from .errors import ProviderConfigurationError
from .types import (
    AccountMetricsResult,
    CancelPublishRequest,
    CancelPublishResult,
    CompleteConnectionRequest,
    CompleteConnectionResult,
    ConnectionUrlRequest,
    ConnectionUrlResult,
    GetPublishStatusRequest,
    GetAccountMetricsRequest,
    GetPostMetricsRequest,
    HealthStatusRequest,
    HealthStatusResult,
    ListSocialAccountsRequest,
    ListSocialAccountsResult,
    ProviderCapabilities,
    ProviderName,
    PublishNowRequest,
    PublishResult,
    PostMetricsResult,
    PublishingMediaType,
    PublishingMetricName,
    PublishingNetwork,
    ValidatePostRequest,
    ValidatePostResult,
    WebhookParseResult,
    WebhookRequest,
)


TARGET_CAPABILITIES = ProviderCapabilities(
    networks=frozenset(PublishingNetwork),
    media_types=frozenset(PublishingMediaType),
    supports_connection_completion=True,
    supports_account_listing=True,
    supports_cancellation=True,
    supports_status_polling=True,
    supports_webhooks=True,
    metric_names=frozenset(PublishingMetricName),
)


class NotImplementedPublishingProvider(PublishingProvider):
    """Non-networking placeholder used until a live provider adapter is added."""

    capabilities = TARGET_CAPABILITIES

    def _not_implemented(self):
        raise ProviderConfigurationError(
            f"{self.provider.value} live publishing is not implemented.",
            safe_details={"provider": self.provider.value},
        )

    def get_connection_url(self, request: ConnectionUrlRequest) -> ConnectionUrlResult:
        return self._not_implemented()

    def complete_connection(self, request: CompleteConnectionRequest) -> CompleteConnectionResult:
        return self._not_implemented()

    def list_social_accounts(self, request: ListSocialAccountsRequest) -> ListSocialAccountsResult:
        return self._not_implemented()

    def validate_post(self, request: ValidatePostRequest) -> ValidatePostResult:
        return self._not_implemented()

    def publish_now(self, request: PublishNowRequest) -> PublishResult:
        return self._not_implemented()

    def cancel_publish(self, request: CancelPublishRequest) -> CancelPublishResult:
        return self._not_implemented()

    def get_publish_status(self, request: GetPublishStatusRequest) -> PublishResult:
        return self._not_implemented()

    def get_post_metrics(self, request: GetPostMetricsRequest) -> PostMetricsResult:
        return self._not_implemented()

    def get_account_metrics(self, request: GetAccountMetricsRequest) -> AccountMetricsResult:
        return self._not_implemented()

    def verify_and_parse_webhook(self, request: WebhookRequest) -> WebhookParseResult:
        return self._not_implemented()

    def health_status(self, request: HealthStatusRequest) -> HealthStatusResult:
        return HealthStatusResult(
            provider=self.provider,
            configured=False,
            healthy=False,
            capabilities=self.capabilities,
            detail="Live adapter not implemented",
            checked_at=datetime.now(timezone.utc),
        )


from .upload_post import UploadPostProvider  # noqa: E402
from .zernio import ZernioProvider  # noqa: E402
