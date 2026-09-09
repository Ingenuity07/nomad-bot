import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from uuid import NAMESPACE_URL, UUID, uuid5

import requests
from django.conf import settings as django_settings

from .contract import PublishingProvider
from .errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderPermanentFailureError,
    ProviderTemporaryFailureError,
    ProviderValidationError,
)
from .http import ProviderHttpClient, ProviderHttpConfig
from .normalization import media_storage_urls, normalize_post_metrics, normalized_post_text, parse_iso_datetime
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
    PublishingMediaType,
    PublishingMetricName,
    PublishingNetwork,
    SocialAccount,
    ValidatePostRequest,
    ValidatePostResult,
    ValidationIssue,
    WebhookParseResult,
    WebhookRequest,
)


ZERNIO_CAPABILITIES = ProviderCapabilities(
    networks=frozenset({PublishingNetwork.LINKEDIN}),
    media_types=frozenset({PublishingMediaType.IMAGE, PublishingMediaType.MULTI_IMAGE}),
    supports_connection_completion=False,
    supports_account_listing=True,
    supports_cancellation=True,
    supports_status_polling=True,
    supports_webhooks=True,
    metric_names=frozenset(PublishingMetricName),
)

LINKEDIN_TEXT_LIMIT = 3000
LINKEDIN_IMAGE_LIMIT = 20
ORGANIZATION_ACCOUNT_TYPES = frozenset({"organization", "organisation", "company", "company_page"})


@dataclass(frozen=True)
class ZernioConfig:
    api_base_url: str
    api_key: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    connection_return_url: str
    timeout_seconds: int = 30

    @classmethod
    def from_django_settings(cls):
        return cls(
            api_base_url=django_settings.ZERNIO_API_BASE_URL,
            api_key=django_settings.ZERNIO_API_KEY,
            webhook_secret=django_settings.ZERNIO_WEBHOOK_SECRET,
            connection_return_url=django_settings.ZERNIO_CONNECTION_RETURN_URL,
            timeout_seconds=django_settings.ZERNIO_HTTP_TIMEOUT_SECONDS,
        )

    @property
    def publishing_configured(self):
        return bool(self.api_base_url and self.api_key and self.connection_return_url)

    @property
    def webhook_configured(self):
        return bool(self.webhook_secret)

    @property
    def fully_configured(self):
        return self.publishing_configured and self.webhook_configured


class ZernioProvider(PublishingProvider):
    provider = ProviderName.ZERNIO
    capabilities = ZERNIO_CAPABILITIES

    def __init__(self, *, session=None, config=None, now=None):
        self._session = session or requests.Session()
        self._config = config or ZernioConfig.from_django_settings()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._profile_cache = {}
        self._http = ProviderHttpClient(
            session=self._session,
            config=ProviderHttpConfig(
                base_url=self._config.api_base_url,
                default_headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout_seconds=self._config.timeout_seconds,
            ),
        )

    @staticmethod
    def profile_name_for_workspace(workspace_id):
        return f"nomad_ws_{workspace_id.hex}"

    @staticmethod
    def _stable_request_id(idempotency_key):
        try:
            return str(UUID(str(idempotency_key)))
        except (TypeError, ValueError, AttributeError):
            return str(uuid5(NAMESPACE_URL, f"nomad-publish:{idempotency_key}"))

    def get_connection_url(self, request: ConnectionUrlRequest) -> ConnectionUrlResult:
        self._require_publishing_configuration()
        requested = request.requested_networks or (PublishingNetwork.LINKEDIN,)
        unsupported = set(requested) - self.capabilities.networks
        if unsupported:
            raise ProviderValidationError(
                "The requested social network is not available for this connection step.",
                safe_details={"networks": sorted(item.value for item in unsupported)},
            )

        profile_id = self._ensure_workspace_profile(request.workspace_id)
        _, payload = self._request(
            "GET",
            "/v1/connect/linkedin",
            params={
                "profileId": profile_id,
                "redirect_url": self._return_url_with_state(request.state),
                "headless": "false",
            },
            allowed_statuses={200},
        )
        auth_url = str(payload.get("authUrl") or "")
        if not auth_url:
            raise ProviderTemporaryFailureError(
                "The social account connection step is temporarily unavailable."
            )
        return ConnectionUrlResult(
            provider=self.provider,
            url=auth_url,
            state=request.state,
        )

    def complete_connection(self, request: CompleteConnectionRequest) -> CompleteConnectionResult:
        self._require_publishing_configuration()
        profile_id = self._ensure_workspace_profile(request.workspace_id)
        accounts = self._list_workspace_accounts(request.workspace_id, profile_id)
        return CompleteConnectionResult(
            provider=self.provider,
            provider_connection_id=profile_id,
            connected=bool(accounts),
            safe_metadata={"account_count": len(accounts)},
        )

    def list_social_accounts(self, request: ListSocialAccountsRequest) -> ListSocialAccountsResult:
        self._require_publishing_configuration()
        accounts = self._list_workspace_accounts(
            request.workspace_id,
            request.provider_connection_id,
        )
        return ListSocialAccountsResult(provider=self.provider, accounts=accounts)

    def validate_post(self, request: ValidatePostRequest) -> ValidatePostResult:
        errors = []
        if request.account.network != request.post.network:
            errors.append(ValidationIssue(
                "NETWORK_MISMATCH", "Post and account networks do not match.", "network"
            ))
        if request.post.network != PublishingNetwork.LINKEDIN:
            errors.append(ValidationIssue(
                "NETWORK_UNSUPPORTED", "This social network is not yet supported.", "network"
            ))
        media_urls = media_storage_urls(request.post.media)
        if not request.post.text.strip() and not media_urls:
            errors.append(ValidationIssue("EMPTY_POST", "A post requires text or media.", "text"))
        for media in request.post.media:
            if media.media_type not in self.capabilities.media_types:
                errors.append(ValidationIssue(
                    "MEDIA_UNSUPPORTED", "This media type is not yet supported.", "media"
                ))
        if len(media_urls) > LINKEDIN_IMAGE_LIMIT:
            errors.append(ValidationIssue(
                "TOO_MANY_IMAGES",
                f"LinkedIn supports at most {LINKEDIN_IMAGE_LIMIT} images per post.",
                "media",
            ))
        for url in media_urls:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(ValidationIssue(
                    "MEDIA_URL_INVALID", "An image has an invalid storage URL.", "media"
                ))
                break
        if len(normalized_post_text(request.post)) > LINKEDIN_TEXT_LIMIT:
            errors.append(ValidationIssue(
                "TEXT_TOO_LONG",
                f"LinkedIn content must be {LINKEDIN_TEXT_LIMIT} characters or fewer.",
                "text",
            ))
        if not request.account.provider_profile_id or not request.account.provider_account_id:
            errors.append(ValidationIssue(
                "ACCOUNT_IDENTIFIER_MISSING",
                "Reconnect the social account before publishing.",
                "account",
            ))
        if request.account.account_type.lower() not in ORGANIZATION_ACCOUNT_TYPES:
            errors.append(ValidationIssue(
                "ACCOUNT_TYPE_UNSUPPORTED",
                "Only LinkedIn Company Pages are currently supported.",
                "account",
            ))
        return ValidatePostResult(valid=not errors, errors=tuple(errors))

    def publish_now(self, request: PublishNowRequest) -> PublishResult:
        self._require_publishing_configuration()
        validation = self.validate_post(ValidatePostRequest(
            account=request.account,
            post=request.post,
        ))
        if not validation.valid:
            raise ProviderValidationError(
                "The post is not valid for the selected social account.",
                safe_details={"codes": [issue.code for issue in validation.errors]},
            )
        self._assert_account_belongs_to_workspace(request.workspace_id, request.account)

        media_urls = media_storage_urls(request.post.media)
        body = {
            "content": normalized_post_text(request.post),
            "platforms": [{
                "platform": "linkedin",
                "accountId": request.account.provider_account_id,
            }],
            "publishNow": True,
            "metadata": {"nomadIdempotencyKey": request.idempotency_key},
        }
        if media_urls:
            body["mediaItems"] = [{"type": "image", "url": url} for url in media_urls]
        response, payload = self._request(
            "POST",
            "/v1/posts",
            json=body,
            headers={"x-request-id": self._stable_request_id(request.idempotency_key)},
            allowed_statuses={200, 201, 403, 409},
            unknown_on_transport_failure=True,
        )
        if response.status_code == 403:
            if str(payload.get("code") or "").upper() == "ACCOUNT_DISCONNECTED":
                return PublishResult(
                    provider=self.provider,
                    outcome=PublishOutcome.FAILED,
                    provider_status="connection_required",
                    error=ProviderErrorInfo(
                        ProviderErrorCategory.AUTHENTICATION,
                        "The social account connection must be refreshed.",
                    ),
                    checked_at=self._now(),
                )
            self._http.raise_for_response(response)
        if response.status_code == 409:
            details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
            return PublishResult(
                provider=self.provider,
                outcome=PublishOutcome.UNKNOWN,
                external_id=str(details.get("existingPostId") or ""),
                provider_status="duplicate",
                error=ProviderErrorInfo(
                    ProviderErrorCategory.UNKNOWN_OUTCOME,
                    "The publishing request already exists and its status must be confirmed.",
                ),
                checked_at=self._now(),
            )
        return self._post_result(payload)

    def cancel_publish(self, request: CancelPublishRequest) -> CancelPublishResult:
        self._require_publishing_configuration()
        response, _ = self._request(
            "DELETE",
            f"/v1/posts/{quote(request.external_id, safe='')}",
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            return CancelPublishResult(
                provider=self.provider,
                cancelled=False,
                outcome=PublishOutcome.UNKNOWN,
                provider_status="not_found",
                error=ProviderErrorInfo(
                    ProviderErrorCategory.UNKNOWN_OUTCOME,
                    "The publishing request was not found.",
                ),
            )
        return CancelPublishResult(
            provider=self.provider,
            cancelled=True,
            outcome=PublishOutcome.FAILED,
            provider_status="cancelled",
        )

    def get_publish_status(self, request: GetPublishStatusRequest) -> PublishResult:
        self._require_publishing_configuration()
        response, payload = self._request(
            "GET",
            f"/v1/posts/{quote(request.external_id, safe='')}",
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            return PublishResult(
                provider=self.provider,
                outcome=PublishOutcome.UNKNOWN,
                external_id=request.external_id,
                provider_status="not_found",
                error=ProviderErrorInfo(
                    ProviderErrorCategory.UNKNOWN_OUTCOME,
                    "The publishing outcome could not be found.",
                ),
                checked_at=self._now(),
            )
        return self._post_result(payload, fallback_external_id=request.external_id)

    def get_post_metrics(self, request: GetPostMetricsRequest) -> PostMetricsResult:
        self._require_publishing_configuration()
        response, payload = self._request(
            "GET",
            "/v1/analytics",
            params={
                "postId": request.external_id,
                "profileId": request.provider_profile_id,
                "accountId": request.provider_account_id,
            },
            allowed_statuses={200, 202, 404, 424},
        )
        if response.status_code != 200:
            return PostMetricsResult(
                provider=self.provider,
                unavailable_metrics=self.capabilities.metric_names,
                checked_at=self._now(),
            )
        posts = payload.get("posts") if isinstance(payload.get("posts"), list) else []
        if posts and isinstance(posts[0], dict):
            row = posts[0]
        elif isinstance(payload.get("post"), dict):
            row = payload["post"]
        else:
            row = payload
        if isinstance(row.get("analytics"), dict):
            metrics = row["analytics"]
        elif isinstance(row.get("metrics"), dict):
            metrics = row["metrics"]
        else:
            metrics = {}
        measured_at = parse_iso_datetime(
            row.get("lastUpdated") or row.get("capturedAt") or payload.get("lastUpdated")
        ) or self._now()
        observations = normalize_post_metrics(
            metrics,
            measured_at=measured_at,
            raw_reference={
                "external_id": request.external_id,
                "provider_post_id": row.get("_id") or row.get("id") or "",
                "platform_post_id": row.get("platformPostId") or "",
            },
        )
        observed = frozenset(item.metric_name for item in observations)
        return PostMetricsResult(
            provider=self.provider,
            observations=observations,
            unavailable_metrics=self.capabilities.metric_names - observed,
            checked_at=measured_at,
        )

    def get_account_metrics(self, request: GetAccountMetricsRequest) -> AccountMetricsResult:
        self._require_publishing_configuration()
        response, payload = self._request(
            "GET",
            "/v1/accounts/follower-stats",
            params={
                "accountIds": request.provider_account_id,
                "profileId": request.provider_profile_id,
            },
            allowed_statuses={200, 403, 404},
        )
        if response.status_code != 200:
            return AccountMetricsResult(
                provider=self.provider,
                unavailable_metrics=frozenset({PublishingMetricName.FOLLOWER_GROWTH}),
                checked_at=self._now(),
            )
        accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
        row = next((
            item for item in accounts
            if isinstance(item, dict) and str(item.get("_id") or item.get("id") or "") == request.provider_account_id
        ), None)
        if row is None and len(accounts) == 1 and isinstance(accounts[0], dict):
            row = accounts[0]
        measured_at = parse_iso_datetime(
            (payload.get("dateRange") or {}).get("to")
            if isinstance(payload.get("dateRange"), dict)
            else None
        ) or self._now()
        observations = normalize_post_metrics(
            {"follower_growth": row.get("growth") if row else None},
            measured_at=measured_at,
            raw_reference={
                "source": "account_follower_stats",
                "granularity": payload.get("granularity") or "",
            },
        )
        observed = frozenset(item.metric_name for item in observations)
        return AccountMetricsResult(
            provider=self.provider,
            observations=observations,
            unavailable_metrics=frozenset({PublishingMetricName.FOLLOWER_GROWTH}) - observed,
            checked_at=measured_at,
        )

    def verify_and_parse_webhook(self, request: WebhookRequest) -> WebhookParseResult:
        if not self._config.webhook_configured:
            raise ProviderConfigurationError("Webhook verification is not configured.")
        headers = {str(key).lower(): str(value) for key, value in request.headers.items()}
        supplied = headers.get("x-zernio-signature") or headers.get("x-late-signature") or ""
        expected = hmac.new(
            self._config.webhook_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        if not supplied or not hmac.compare_digest(supplied.lower(), expected):
            raise ProviderAuthenticationError("The webhook signature is invalid.")
        try:
            payload = json.loads(request.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderValidationError("The webhook body is invalid.") from exc
        if not isinstance(payload, dict):
            raise ProviderValidationError("The webhook body is invalid.")

        event_type = str(payload.get("event") or "unknown")
        post = payload.get("post") if isinstance(payload.get("post"), dict) else {}
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        metadata = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
        event_id = str(payload.get("id") or headers.get("x-zernio-event-id") or "")
        if not event_id:
            raise ProviderValidationError("The webhook event identifier is missing.")
        outcome = self._event_outcome(event_type)
        external_id = str(
            post.get("_id")
            or post.get("id")
            or payload.get("postId")
            or account.get("accountId")
            or account.get("id")
            or ""
        )
        platform_entries = post.get("platforms") if isinstance(post.get("platforms"), list) else []
        first_platform = platform_entries[0] if platform_entries and isinstance(platform_entries[0], dict) else {}
        event = NormalizedWebhookEvent(
            event_type=event_type,
            outcome=outcome,
            external_id=external_id,
            idempotency_key=str(metadata.get("nomadIdempotencyKey") or ""),
            occurred_at=parse_iso_datetime(payload.get("timestamp") or payload.get("createdAt")),
            safe_metadata={
                "event_id": event_id,
                "profile_id": account.get("profileId") or payload.get("profileId") or "",
                "account_id": account.get("accountId") or account.get("id") or first_platform.get("accountId") or "",
                "network": account.get("platform") or first_platform.get("platform") or "",
                "post_url": first_platform.get("platformPostUrl") or "",
            },
        )
        return WebhookParseResult(provider=self.provider, verified=True, events=(event,))

    def health_status(self, request: HealthStatusRequest) -> HealthStatusResult:
        if not self._config.fully_configured:
            return HealthStatusResult(
                provider=self.provider,
                configured=False,
                healthy=False,
                capabilities=self.capabilities,
                detail="Publishing connection is not fully configured.",
                checked_at=self._now(),
            )
        try:
            self._request("GET", "/v1/profiles", allowed_statuses={200})
        except ProviderAuthenticationError:
            return HealthStatusResult(
                provider=self.provider,
                configured=True,
                healthy=False,
                capabilities=self.capabilities,
                detail="Publishing credentials were rejected.",
                checked_at=self._now(),
            )
        except (ProviderTemporaryFailureError, ProviderPermanentFailureError):
            return HealthStatusResult(
                provider=self.provider,
                configured=True,
                healthy=False,
                capabilities=self.capabilities,
                detail="Publishing service health check failed.",
                checked_at=self._now(),
            )
        return HealthStatusResult(
            provider=self.provider,
            configured=True,
            healthy=True,
            capabilities=self.capabilities,
            detail="Publishing service is ready.",
            checked_at=self._now(),
        )

    def _require_publishing_configuration(self):
        if not self._config.publishing_configured:
            raise ProviderConfigurationError("Publishing service configuration is incomplete.")

    def _request(self, method, path, **kwargs):
        return self._http.request(method, path, **kwargs)

    def _ensure_workspace_profile(self, workspace_id):
        cached = self._profile_cache.get(workspace_id)
        if cached:
            return cached
        profile_name = self.profile_name_for_workspace(workspace_id)
        response, payload = self._request(
            "POST",
            "/v1/profiles",
            json={
                "name": profile_name,
                "description": "Content Studio customer workspace",
            },
            headers={"Idempotency-Key": str(workspace_id)},
            allowed_statuses={201, 409},
        )
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        profile_id = str(
            profile.get("_id")
            or profile.get("id")
            or details.get("existingProfileId")
            or payload.get("existingProfileId")
            or ""
        )
        if response.status_code == 409 and not profile_id:
            _, listing = self._request("GET", "/v1/profiles", allowed_statuses={200})
            for candidate in listing.get("profiles") or []:
                if isinstance(candidate, dict) and candidate.get("name") == profile_name:
                    profile_id = str(candidate.get("_id") or candidate.get("id") or "")
                    break
        if not profile_id:
            raise ProviderTemporaryFailureError(
                "The customer publishing profile could not be prepared."
            )
        self._profile_cache[workspace_id] = profile_id
        return profile_id

    def _assert_workspace_profile(self, workspace_id, profile_id):
        cached = self._profile_cache.get(workspace_id)
        if cached:
            if not hmac.compare_digest(str(cached), str(profile_id)):
                raise ProviderAuthenticationError(
                    "The social account connection does not belong to this workspace."
                )
            return
        response, payload = self._request(
            "GET",
            f"/v1/profiles/{quote(str(profile_id), safe='')}",
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            raise ProviderAuthenticationError(
                "The social account connection does not belong to this workspace."
            )
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
        expected_name = self.profile_name_for_workspace(workspace_id)
        if not hmac.compare_digest(str(profile.get("name") or ""), expected_name):
            raise ProviderAuthenticationError(
                "The social account connection does not belong to this workspace."
            )
        self._profile_cache[workspace_id] = str(profile_id)

    def _list_workspace_accounts(self, workspace_id, profile_id):
        self._assert_workspace_profile(workspace_id, profile_id)
        _, payload = self._request(
            "GET",
            "/v1/accounts",
            params={
                "profileId": profile_id,
                "platform": "linkedin",
                "status": "connected",
            },
            allowed_statuses={200},
        )
        accounts = []
        for item in payload.get("accounts") or []:
            if not isinstance(item, dict) or str(item.get("platform") or "").lower() != "linkedin":
                continue
            if item.get("isActive") is False:
                continue
            account_type = str(item.get("accountType") or "").lower()
            if account_type not in ORGANIZATION_ACCOUNT_TYPES:
                continue
            item_profile = item.get("profileId")
            item_profile_id = (
                str(item_profile.get("_id") or item_profile.get("id") or "")
                if isinstance(item_profile, dict)
                else str(item_profile or profile_id)
            )
            if not hmac.compare_digest(item_profile_id, str(profile_id)):
                continue
            account_id = str(item.get("_id") or item.get("accountId") or item.get("id") or "")
            if not account_id:
                continue
            accounts.append(SocialAccount(
                provider_profile_id=str(profile_id),
                provider_account_id=account_id,
                network=PublishingNetwork.LINKEDIN,
                display_name=str(item.get("displayName") or item.get("username") or "LinkedIn Company Page"),
                account_type="ORGANIZATION",
                capabilities=self.capabilities,
            ))
        return tuple(accounts)

    def _assert_account_belongs_to_workspace(self, workspace_id, account):
        accounts = self._list_workspace_accounts(workspace_id, account.provider_profile_id)
        if not any(
            hmac.compare_digest(item.provider_account_id, account.provider_account_id)
            for item in accounts
        ):
            raise ProviderAuthenticationError(
                "The social account does not belong to this workspace."
            )

    def _return_url_with_state(self, state):
        parsed = urlparse(self._config.connection_return_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("The social account return URL is invalid.")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["state"] = state
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _post_result(self, payload, fallback_external_id=""):
        post = payload.get("post") if isinstance(payload.get("post"), dict) else {}
        if not post and isinstance(payload.get("existingPost"), dict):
            post = payload["existingPost"]
        external_id = str(post.get("_id") or post.get("id") or fallback_external_id)
        status = str(post.get("status") or "unknown").lower()
        outcome = self._status_outcome(status)
        error = None
        if outcome == PublishOutcome.FAILED:
            error = ProviderErrorInfo(
                ProviderErrorCategory.PERMANENT_FAILURE,
                "The publishing request failed.",
            )
        elif outcome == PublishOutcome.UNKNOWN:
            error = ProviderErrorInfo(
                ProviderErrorCategory.UNKNOWN_OUTCOME,
                "The publishing outcome is unknown.",
            )
        platform_entries = post.get("platforms") if isinstance(post.get("platforms"), list) else []
        linkedin = next((
            item for item in platform_entries
            if isinstance(item, dict) and str(item.get("platform") or "").lower() == "linkedin"
        ), {})
        return PublishResult(
            provider=self.provider,
            outcome=outcome,
            external_id=external_id,
            provider_status=status,
            error=error,
            checked_at=self._now(),
            safe_metadata={"post_url": linkedin.get("platformPostUrl") or post.get("platformPostUrl") or ""},
        )

    @staticmethod
    def _status_outcome(status):
        if status == "published":
            return PublishOutcome.PUBLISHED
        if status in {"scheduled", "publishing", "pending", "queued", "processing"}:
            return PublishOutcome.ACCEPTED
        if status in {"failed", "partial", "cancelled"}:
            return PublishOutcome.FAILED
        return PublishOutcome.UNKNOWN

    @classmethod
    def _event_outcome(cls, event_type):
        if event_type in {"post.published", "post.platform.published"}:
            return PublishOutcome.PUBLISHED
        if event_type in {
            "post.failed", "post.partial", "post.cancelled", "post.platform.failed"
        }:
            return PublishOutcome.FAILED
        if event_type in {"post.scheduled", "post.recycled"}:
            return PublishOutcome.ACCEPTED
        return PublishOutcome.UNKNOWN
