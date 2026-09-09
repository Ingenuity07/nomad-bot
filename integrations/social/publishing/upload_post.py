import hashlib
import hmac
import json
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

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


UPLOAD_POST_CAPABILITIES = ProviderCapabilities(
    networks=frozenset({PublishingNetwork.LINKEDIN}),
    media_types=frozenset({PublishingMediaType.IMAGE, PublishingMediaType.MULTI_IMAGE}),
    supports_connection_completion=False,
    supports_account_listing=True,
    supports_cancellation=True,
    supports_status_polling=True,
    supports_webhooks=True,
    metric_names=frozenset(PublishingMetricName) - {PublishingMetricName.FOLLOWER_GROWTH},
)

MAX_LINKEDIN_IMAGE_BYTES = 8 * 1024 * 1024
WEBHOOK_MAX_AGE_SECONDS = 300


@dataclass(frozen=True)
class UploadPostConfig:
    api_base_url: str
    api_key: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    connection_return_url: str
    timeout_seconds: int = 30

    @classmethod
    def from_django_settings(cls):
        return cls(
            api_base_url=django_settings.UPLOAD_POST_API_BASE_URL,
            api_key=django_settings.UPLOAD_POST_API_KEY,
            webhook_secret=django_settings.UPLOAD_POST_WEBHOOK_SECRET,
            connection_return_url=django_settings.UPLOAD_POST_CONNECTION_RETURN_URL,
            timeout_seconds=django_settings.UPLOAD_POST_HTTP_TIMEOUT_SECONDS,
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


class UploadPostProvider(PublishingProvider):
    provider = ProviderName.UPLOAD_POST
    capabilities = UPLOAD_POST_CAPABILITIES

    def __init__(self, *, session=None, config=None, now=None):
        self._session = session or requests.Session()
        self._config = config or UploadPostConfig.from_django_settings()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._http = ProviderHttpClient(
            session=self._session,
            config=ProviderHttpConfig(
                base_url=self._config.api_base_url,
                default_headers={"Authorization": f"Apikey {self._config.api_key}"},
                timeout_seconds=self._config.timeout_seconds,
            ),
        )

    @staticmethod
    def profile_id_for_workspace(workspace_id):
        return f"nomad_ws_{workspace_id.hex}"

    def get_connection_url(self, request: ConnectionUrlRequest) -> ConnectionUrlResult:
        self._require_publishing_configuration()
        requested = request.requested_networks or (PublishingNetwork.LINKEDIN,)
        unsupported = set(requested) - self.capabilities.networks
        if unsupported:
            raise ProviderValidationError(
                "The requested social network is not available for this connection step.",
                safe_details={"networks": sorted(item.value for item in unsupported)},
            )

        profile_id = self.profile_id_for_workspace(request.workspace_id)
        response, _ = self._request(
            "POST",
            "/uploadposts/users",
            json={"username": profile_id},
            allowed_statuses={201, 409},
        )
        if response.status_code not in {201, 409}:
            self._http.raise_for_response(response)

        redirect_url = self._return_url_with_state(request.state)
        _, payload = self._request(
            "POST",
            "/uploadposts/users/generate-jwt",
            json={
                "username": profile_id,
                "redirect_url": redirect_url,
                "platforms": ["linkedin"],
                "show_calendar": False,
                "connect_title": "Social account connection",
                "connect_description": "Connect the social account used for publishing.",
                "redirect_button_text": "Return to Content Studio",
            },
            allowed_statuses={200},
        )
        access_url = str(payload.get("access_url") or "")
        if not access_url:
            raise ProviderTemporaryFailureError("The social account connection step is temporarily unavailable.")
        return ConnectionUrlResult(
            provider=self.provider,
            url=access_url,
            state=request.state,
            expires_at=self._now() + timedelta(hours=48),
        )

    def complete_connection(self, request: CompleteConnectionRequest) -> CompleteConnectionResult:
        profile_id = self.profile_id_for_workspace(request.workspace_id)
        accounts = self.list_social_accounts(ListSocialAccountsRequest(
            workspace_id=request.workspace_id,
            provider_connection_id=profile_id,
        ))
        return CompleteConnectionResult(
            provider=self.provider,
            provider_connection_id=profile_id,
            connected=bool(accounts.accounts),
            safe_metadata={"account_count": len(accounts.accounts)},
        )

    def list_social_accounts(self, request: ListSocialAccountsRequest) -> ListSocialAccountsResult:
        self._require_publishing_configuration()
        expected_profile_id = self.profile_id_for_workspace(request.workspace_id)
        if not hmac.compare_digest(request.provider_connection_id, expected_profile_id):
            raise ProviderAuthenticationError("The social account connection does not belong to this workspace.")
        response, payload = self._request(
            "GET",
            "/uploadposts/linkedin/pages",
            params={"profile": expected_profile_id},
            allowed_statuses={200, 404},
        )
        if response.status_code == 404:
            return ListSocialAccountsResult(provider=self.provider, accounts=())
        accounts = []
        for page in payload.get("pages") or []:
            page_id = str(page.get("id") or "")
            if not page_id:
                continue
            accounts.append(SocialAccount(
                provider_profile_id=expected_profile_id,
                provider_account_id=page_id,
                network=PublishingNetwork.LINKEDIN,
                display_name=str(page.get("name") or "LinkedIn Company Page"),
                account_type="ORGANIZATION",
                capabilities=self.capabilities,
            ))
        return ListSocialAccountsResult(provider=self.provider, accounts=tuple(accounts))

    def validate_post(self, request: ValidatePostRequest) -> ValidatePostResult:
        errors = []
        if request.account.network != request.post.network:
            errors.append(ValidationIssue("NETWORK_MISMATCH", "Post and account networks do not match.", "network"))
        if request.post.network != PublishingNetwork.LINKEDIN:
            errors.append(ValidationIssue("NETWORK_UNSUPPORTED", "This social network is not yet supported.", "network"))
        media_urls = media_storage_urls(request.post.media)
        if not request.post.text.strip() and not media_urls:
            errors.append(ValidationIssue("EMPTY_POST", "A post requires text or media.", "text"))
        for media in request.post.media:
            if media.media_type not in self.capabilities.media_types:
                errors.append(ValidationIssue("MEDIA_UNSUPPORTED", "This media type is not yet supported.", "media"))
        text_limit = 1024 if media_urls else 3000
        if len(normalized_post_text(request.post)) > text_limit:
            errors.append(ValidationIssue(
                "TEXT_TOO_LONG",
                f"LinkedIn content must be {text_limit} characters or fewer for this post type.",
                "text",
            ))
        if not request.account.provider_profile_id or not request.account.provider_account_id:
            errors.append(ValidationIssue(
                "ACCOUNT_IDENTIFIER_MISSING",
                "Reconnect the social account before publishing.",
                "account",
            ))
        return ValidatePostResult(valid=not errors, errors=tuple(errors))

    def publish_now(self, request: PublishNowRequest) -> PublishResult:
        self._require_publishing_configuration()
        validation = self.validate_post(ValidatePostRequest(account=request.account, post=request.post))
        if not validation.valid:
            raise ProviderValidationError(
                "The post is not valid for the selected social account.",
                safe_details={"codes": [issue.code for issue in validation.errors]},
            )

        headers = {
            "Idempotency-Key": request.idempotency_key,
            "X-External-Id": request.idempotency_key,
        }
        data = [
            ("user", request.account.provider_profile_id),
            ("platform[]", "linkedin"),
            ("title", normalized_post_text(request.post)),
            ("target_linkedin_page_id", request.account.provider_account_id),
            ("async_upload", "true"),
            ("request_id", request.idempotency_key),
            ("external_id", request.idempotency_key),
        ]
        media_urls = media_storage_urls(request.post.media)
        if media_urls:
            data.append(("description", normalized_post_text(request.post)))
            files = [self._download_image(url) for url in media_urls]
            _, payload = self._request(
                "POST",
                "/upload_photos",
                data=data,
                files=files,
                headers=headers,
                allowed_statuses={200, 202},
                unknown_on_transport_failure=True,
            )
        else:
            _, payload = self._request(
                "POST",
                "/upload_text",
                data=data,
                headers=headers,
                allowed_statuses={200, 202},
                unknown_on_transport_failure=True,
            )
        return self._publish_result(payload)

    def cancel_publish(self, request: CancelPublishRequest) -> CancelPublishResult:
        self._require_publishing_configuration()
        response, payload = self._request(
            "DELETE",
            f"/uploadposts/schedule/{quote(request.external_id, safe='')}",
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
            cancelled=bool(payload.get("success", True)),
            outcome=PublishOutcome.FAILED,
            provider_status="cancelled",
        )

    def get_publish_status(self, request: GetPublishStatusRequest) -> PublishResult:
        self._require_publishing_configuration()
        response, payload = self._request(
            "GET",
            "/uploadposts/status",
            params={"request_id": request.external_id},
            allowed_statuses={200, 404},
        )
        if response.status_code == 404 or str(payload.get("status", "")).lower() == "not_found":
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
        return self._status_result(payload, request.external_id)

    def get_post_metrics(self, request: GetPostMetricsRequest) -> PostMetricsResult:
        self._require_publishing_configuration()
        response, payload = self._request(
            "GET",
            f"/uploadposts/post-analytics/{quote(request.external_id, safe='')}",
            allowed_statuses={200, 202, 404},
        )
        if response.status_code != 200:
            return PostMetricsResult(
                provider=self.provider,
                unavailable_metrics=self.capabilities.metric_names,
                checked_at=self._now(),
            )
        platforms = payload.get("platforms") if isinstance(payload.get("platforms"), dict) else {}
        platform = platforms.get("linkedin") if isinstance(platforms.get("linkedin"), dict) else {}
        if isinstance(platform.get("post_metrics"), dict):
            metrics = platform["post_metrics"]
        elif isinstance(payload.get("post_metrics"), dict):
            metrics = payload["post_metrics"]
        elif isinstance(payload.get("metrics"), dict):
            metrics = payload["metrics"]
        else:
            metrics = {}
        measured_at = parse_iso_datetime(
            payload.get("captured_at") or platform.get("captured_at") or payload.get("updated_at")
        ) or self._now()
        observations = normalize_post_metrics(
            metrics,
            measured_at=measured_at,
            raw_reference={
                "external_id": request.external_id,
                "platform_post_id": platform.get("platform_post_id") or payload.get("platform_post_id") or "",
                "source": platform.get("post_metrics_source") or payload.get("post_metrics_source") or "",
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
        # Current account analytics expose follower totals, not a defensible growth
        # value for the LinkedIn, X, and Instagram connections supported here.
        self._require_publishing_configuration()
        return AccountMetricsResult(
            provider=self.provider,
            unavailable_metrics=frozenset({PublishingMetricName.FOLLOWER_GROWTH}),
            checked_at=self._now(),
        )

    def verify_and_parse_webhook(self, request: WebhookRequest) -> WebhookParseResult:
        if not self._config.webhook_configured:
            raise ProviderConfigurationError("Webhook verification is not configured.")
        headers = {str(key).lower(): str(value) for key, value in request.headers.items()}
        signature = headers.get("x-upload-post-signature", "")
        timestamp_text = headers.get("x-upload-post-timestamp", "")
        try:
            timestamp = int(timestamp_text)
        except (TypeError, ValueError) as exc:
            raise ProviderAuthenticationError("The webhook timestamp is invalid.") from exc
        received_at = request.received_at or self._now()
        if abs(received_at.timestamp() - timestamp) > WEBHOOK_MAX_AGE_SECONDS:
            raise ProviderAuthenticationError("The webhook timestamp is outside the allowed window.")
        expected = hmac.new(
            self._config.webhook_secret.encode(),
            f"{timestamp}.".encode() + request.body,
            hashlib.sha256,
        ).hexdigest()
        supplied = signature.removeprefix("sha256=")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise ProviderAuthenticationError("The webhook signature is invalid.")
        try:
            payload = json.loads(request.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderValidationError("The webhook body is invalid.") from exc
        if not isinstance(payload, dict):
            raise ProviderValidationError("The webhook body is invalid.")
        event_type = str(payload.get("event") or headers.get("x-upload-post-event") or "unknown")
        event = self._normalize_webhook_event(
            event_type,
            payload,
            headers.get("x-upload-post-delivery", ""),
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
            self._request("GET", "/uploadposts/me", allowed_statuses={200})
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

    def _request(
        self,
        method,
        path,
        *,
        allowed_statuses,
        unknown_on_transport_failure=False,
        headers=None,
        **kwargs,
    ):
        return self._http.request(
            method,
            path,
            allowed_statuses=allowed_statuses,
            unknown_on_transport_failure=unknown_on_transport_failure,
            headers=headers,
            **kwargs,
        )

    def _download_image(self, url):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderValidationError("An image has an invalid storage URL.")
        try:
            response = self._session.get(url, timeout=self._config.timeout_seconds)
        except requests.RequestException as exc:
            raise ProviderTemporaryFailureError("An image could not be prepared for publishing.") from exc
        if response.status_code != 200:
            raise ProviderTemporaryFailureError("An image could not be prepared for publishing.")
        content = response.content
        if len(content) > MAX_LINKEDIN_IMAGE_BYTES:
            raise ProviderValidationError("A LinkedIn image must be 8 MB or smaller.")
        filename = PurePosixPath(parsed.path).name or "image.jpg"
        content_type = response.headers.get("Content-Type") or mimetypes.guess_type(filename)[0] or "image/jpeg"
        return ("photos[]", (filename, content, content_type))

    def _return_url_with_state(self, state):
        parsed = urlparse(self._config.connection_return_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderConfigurationError("The social account return URL is invalid.")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["state"] = state
        return urlunparse(parsed._replace(query=urlencode(query)))

    def _publish_result(self, payload):
        external_id = str(payload.get("request_id") or payload.get("job_id") or "")
        if external_id:
            return PublishResult(
                provider=self.provider,
                outcome=PublishOutcome.ACCEPTED,
                external_id=external_id,
                provider_status="accepted",
                checked_at=self._now(),
            )
        result = (payload.get("results") or {}).get("linkedin") or payload
        success = bool(result.get("success"))
        external_id = str(
            result.get("publish_id")
            or result.get("post_id")
            or result.get("id")
            or ""
        )
        if success:
            return PublishResult(
                provider=self.provider,
                outcome=PublishOutcome.PUBLISHED,
                external_id=external_id,
                provider_status="published",
                checked_at=self._now(),
                safe_metadata={"post_url": result.get("url") or ""},
            )
        connection_required = bool(result.get("skipped")) or result.get("error_code") == "profile_platform_mapping_invalid"
        category = (
            ProviderErrorCategory.AUTHENTICATION
            if connection_required
            else ProviderErrorCategory.PERMANENT_FAILURE
        )
        message = (
            "The social account connection must be refreshed."
            if connection_required
            else "The publishing request failed."
        )
        return PublishResult(
            provider=self.provider,
            outcome=PublishOutcome.FAILED,
            external_id=external_id,
            provider_status="failed",
            error=ProviderErrorInfo(category, message),
            checked_at=self._now(),
        )

    def _status_result(self, payload, fallback_external_id):
        status = str(payload.get("status") or "unknown").lower()
        external_id = str(payload.get("request_id") or payload.get("job_id") or fallback_external_id)
        if status == "completed":
            results = payload.get("results") or []
            failed = any(not item.get("success", False) for item in results) if results else False
            outcome = PublishOutcome.FAILED if failed else PublishOutcome.PUBLISHED
        elif status == "failed":
            outcome = PublishOutcome.FAILED
        elif status in {"pending", "queued", "processing", "in_progress"}:
            outcome = PublishOutcome.ACCEPTED
        else:
            outcome = PublishOutcome.UNKNOWN
        error = None
        if outcome == PublishOutcome.FAILED:
            error = ProviderErrorInfo(ProviderErrorCategory.PERMANENT_FAILURE, "The publishing request failed.")
        elif outcome == PublishOutcome.UNKNOWN:
            error = ProviderErrorInfo(ProviderErrorCategory.UNKNOWN_OUTCOME, "The publishing outcome is unknown.")
        return PublishResult(
            provider=self.provider,
            outcome=outcome,
            external_id=external_id,
            provider_status=status,
            error=error,
            checked_at=self._now(),
        )

    def _normalize_webhook_event(self, event_type, payload, delivery_id):
        occurred_at = parse_iso_datetime(payload.get("created_at"))
        if event_type == "upload_completed":
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            succeeded = bool(result.get("success"))
            return NormalizedWebhookEvent(
                event_type=event_type,
                outcome=PublishOutcome.PUBLISHED if succeeded else PublishOutcome.FAILED,
                external_id=str(
                    result.get("publish_id")
                    or result.get("post_id")
                    or payload.get("job_id")
                    or ""
                ),
                idempotency_key=str(payload.get("external_id") or ""),
                occurred_at=occurred_at,
                safe_metadata={
                    "delivery_id": delivery_id,
                    "profile_id": payload.get("profile_username") or "",
                    "network": payload.get("platform") or "",
                    "post_url": result.get("url") or "",
                },
            )
        return NormalizedWebhookEvent(
            event_type=event_type,
            outcome=PublishOutcome.UNKNOWN,
            external_id=str(payload.get("account_name") or ""),
            occurred_at=occurred_at,
            safe_metadata={
                "delivery_id": delivery_id,
                "profile_id": payload.get("profile_username") or "",
                "network": payload.get("platform") or "",
                "connection_status": payload.get("status") or "",
                "reason": payload.get("reason") or "",
            },
        )
