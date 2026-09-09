from abc import ABC, abstractmethod

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
    PostMetricsResult,
    PublishNowRequest,
    PublishResult,
    ValidatePostRequest,
    ValidatePostResult,
    WebhookParseResult,
    WebhookRequest,
)


class PublishingProvider(ABC):
    """Pure provider boundary. Implementations must never mutate Django models."""

    provider: ProviderName
    capabilities: ProviderCapabilities

    @abstractmethod
    def get_connection_url(self, request: ConnectionUrlRequest) -> ConnectionUrlResult:
        raise NotImplementedError

    @abstractmethod
    def complete_connection(self, request: CompleteConnectionRequest) -> CompleteConnectionResult:
        raise NotImplementedError

    @abstractmethod
    def list_social_accounts(self, request: ListSocialAccountsRequest) -> ListSocialAccountsResult:
        raise NotImplementedError

    @abstractmethod
    def validate_post(self, request: ValidatePostRequest) -> ValidatePostResult:
        raise NotImplementedError

    @abstractmethod
    def publish_now(self, request: PublishNowRequest) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_publish(self, request: CancelPublishRequest) -> CancelPublishResult:
        raise NotImplementedError

    @abstractmethod
    def get_publish_status(self, request: GetPublishStatusRequest) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def get_post_metrics(self, request: GetPostMetricsRequest) -> PostMetricsResult:
        raise NotImplementedError

    @abstractmethod
    def get_account_metrics(self, request: GetAccountMetricsRequest) -> AccountMetricsResult:
        raise NotImplementedError

    @abstractmethod
    def verify_and_parse_webhook(self, request: WebhookRequest) -> WebhookParseResult:
        raise NotImplementedError

    @abstractmethod
    def health_status(self, request: HealthStatusRequest) -> HealthStatusResult:
        raise NotImplementedError
