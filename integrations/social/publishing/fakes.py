import json
from datetime import datetime, timezone

from .adapters import TARGET_CAPABILITIES
from .contract import PublishingProvider
from .errors import ProviderAuthenticationError, ProviderValidationError
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
    NormalizedWebhookEvent,
    PostMetricsResult,
    ProviderCapabilities,
    ProviderErrorCategory,
    ProviderErrorInfo,
    ProviderName,
    PublishNowRequest,
    PublishOutcome,
    PublishResult,
    SocialAccount,
    ValidatePostRequest,
    ValidatePostResult,
    ValidationIssue,
    WebhookParseResult,
    WebhookRequest,
)


class FakePublishingProvider(PublishingProvider):
    provider: ProviderName

    def __init__(
        self,
        *,
        accounts=(),
        capabilities: ProviderCapabilities = TARGET_CAPABILITIES,
        webhook_signature="valid-fake-signature",
    ):
        self.accounts = tuple(accounts)
        self.capabilities = capabilities
        self.webhook_signature = webhook_signature
        self._results: dict[str, PublishResult] = {}
        self._external_by_idempotency_key: dict[str, str] = {}
        self._next_outcome = PublishOutcome.ACCEPTED
        self._metrics_result = PostMetricsResult(provider=self.provider)
        self._account_metrics_result = AccountMetricsResult(provider=self.provider)
        self.calls: list[str] = []

    def set_next_outcome(self, outcome: PublishOutcome):
        self._next_outcome = outcome

    def set_publish_outcome(self, external_id: str, outcome: PublishOutcome):
        self._results[external_id] = self._result(external_id, outcome)

    def set_metrics_result(self, result: PostMetricsResult):
        self._metrics_result = result

    def set_account_metrics_result(self, result: AccountMetricsResult):
        self._account_metrics_result = result

    def get_connection_url(self, request: ConnectionUrlRequest) -> ConnectionUrlResult:
        self.calls.append("get_connection_url")
        return ConnectionUrlResult(
            provider=self.provider,
            url=f"https://fake.invalid/{self.provider.value.lower()}/connect?state={request.state}",
            state=request.state,
        )

    def complete_connection(self, request: CompleteConnectionRequest) -> CompleteConnectionResult:
        self.calls.append("complete_connection")
        if not request.authorization_code:
            raise ProviderAuthenticationError("The authorization code is missing.")
        return CompleteConnectionResult(
            provider=self.provider,
            provider_connection_id=f"fake-{self.provider.value.lower()}-connection",
            connected=True,
        )

    def list_social_accounts(self, request: ListSocialAccountsRequest) -> ListSocialAccountsResult:
        self.calls.append("list_social_accounts")
        return ListSocialAccountsResult(provider=self.provider, accounts=self.accounts)

    def validate_post(self, request: ValidatePostRequest) -> ValidatePostResult:
        self.calls.append("validate_post")
        errors = []
        if request.post.network != request.account.network:
            errors.append(ValidationIssue("NETWORK_MISMATCH", "Post and account networks do not match.", "network"))
        if request.post.network not in self.capabilities.networks:
            errors.append(ValidationIssue("NETWORK_UNSUPPORTED", "The network is not supported.", "network"))
        if not request.post.text.strip() and not request.post.media:
            errors.append(ValidationIssue("EMPTY_POST", "A post requires text or media.", "text"))
        for media in request.post.media:
            if media.media_type not in self.capabilities.media_types:
                errors.append(ValidationIssue("MEDIA_UNSUPPORTED", "The media type is not supported.", "media"))
        return ValidatePostResult(valid=not errors, errors=tuple(errors))

    def publish_now(self, request: PublishNowRequest) -> PublishResult:
        self.calls.append("publish_now")
        validation = self.validate_post(ValidatePostRequest(account=request.account, post=request.post))
        if not validation.valid:
            raise ProviderValidationError(
                "The normalized post is invalid.",
                safe_details={"codes": [issue.code for issue in validation.errors]},
            )
        existing_external_id = self._external_by_idempotency_key.get(request.idempotency_key)
        if existing_external_id:
            return self._results[existing_external_id]
        external_id = f"fake-{self.provider.value.lower()}-{len(self._results) + 1}"
        result = self._result(external_id, self._next_outcome)
        self._external_by_idempotency_key[request.idempotency_key] = external_id
        self._results[external_id] = result
        self._next_outcome = PublishOutcome.ACCEPTED
        return result

    def cancel_publish(self, request: CancelPublishRequest) -> CancelPublishResult:
        self.calls.append("cancel_publish")
        existing = self._results.get(request.external_id)
        if existing is None or existing.outcome == PublishOutcome.PUBLISHED:
            return CancelPublishResult(
                provider=self.provider,
                cancelled=False,
                outcome=PublishOutcome.UNKNOWN if existing is None else PublishOutcome.PUBLISHED,
                provider_status="not_cancellable",
            )
        self._results[request.external_id] = self._result(request.external_id, PublishOutcome.FAILED, "cancelled")
        return CancelPublishResult(
            provider=self.provider,
            cancelled=True,
            outcome=PublishOutcome.FAILED,
            provider_status="cancelled",
        )

    def get_publish_status(self, request: GetPublishStatusRequest) -> PublishResult:
        self.calls.append("get_publish_status")
        return self._results.get(request.external_id) or self._result(
            request.external_id,
            PublishOutcome.UNKNOWN,
            "not_found",
        )

    def get_post_metrics(self, request: GetPostMetricsRequest) -> PostMetricsResult:
        self.calls.append("get_post_metrics")
        return self._metrics_result

    def get_account_metrics(self, request: GetAccountMetricsRequest) -> AccountMetricsResult:
        self.calls.append("get_account_metrics")
        return self._account_metrics_result

    def verify_and_parse_webhook(self, request: WebhookRequest) -> WebhookParseResult:
        self.calls.append("verify_and_parse_webhook")
        if request.headers.get("X-Fake-Signature") != self.webhook_signature:
            raise ProviderAuthenticationError("The webhook signature is invalid.")
        try:
            payload = json.loads(request.body)
            outcome = PublishOutcome(str(payload.get("outcome", "UNKNOWN")).upper())
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderValidationError("The webhook body is invalid.") from exc
        event = NormalizedWebhookEvent(
            event_type=str(payload.get("event_type") or "publish.updated"),
            outcome=outcome,
            external_id=str(payload.get("external_id") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            occurred_at=request.received_at,
            safe_metadata={
                key: str(payload[key])
                for key in ("event_id", "profile_id", "account_id")
                if payload.get(key)
            },
        )
        return WebhookParseResult(provider=self.provider, verified=True, events=(event,))

    def health_status(self, request: HealthStatusRequest) -> HealthStatusResult:
        self.calls.append("health_status")
        return HealthStatusResult(
            provider=self.provider,
            configured=True,
            healthy=True,
            capabilities=self.capabilities,
            detail="Fake provider ready",
            checked_at=datetime.now(timezone.utc),
        )

    def _result(self, external_id, outcome, provider_status=""):
        error = None
        if outcome == PublishOutcome.FAILED:
            error = ProviderErrorInfo(
                category=ProviderErrorCategory.PERMANENT_FAILURE,
                message="Fake provider failure",
            )
        return PublishResult(
            provider=self.provider,
            outcome=outcome,
            external_id=external_id,
            provider_status=provider_status or outcome.value.lower(),
            error=error,
            checked_at=datetime.now(timezone.utc),
        )


class FakeUploadPostProvider(FakePublishingProvider):
    provider = ProviderName.UPLOAD_POST


class FakeZernioProvider(FakePublishingProvider):
    provider = ProviderName.ZERNIO
