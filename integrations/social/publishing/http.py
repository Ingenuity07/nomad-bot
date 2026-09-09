"""Provider-neutral HTTP safety and normalized error handling."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping

import requests

from .errors import (
    ProviderAuthenticationError,
    ProviderPermanentFailureError,
    ProviderRateLimitError,
    ProviderTemporaryFailureError,
    ProviderUnknownOutcomeError,
    ProviderValidationError,
)


@dataclass(frozen=True)
class ProviderHttpConfig:
    base_url: str
    default_headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: int = 30

    def __repr__(self):
        return (
            f"ProviderHttpConfig(base_url={self.base_url!r}, "
            f"default_headers=<redacted>, timeout_seconds={self.timeout_seconds!r})"
        )


class ProviderHttpClient:
    """Small requests wrapper that never includes response bodies in errors."""

    def __init__(self, *, session, config: ProviderHttpConfig):
        self._session = session
        self._config = config

    def request(
        self,
        method,
        path,
        *,
        allowed_statuses,
        unknown_on_transport_failure=False,
        headers=None,
        **kwargs,
    ):
        request_headers = {
            **self._config.default_headers,
            **(headers or {}),
        }
        try:
            response = self._session.request(
                method,
                f"{self._config.base_url}{path}",
                headers=request_headers,
                timeout=self._config.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            if unknown_on_transport_failure:
                raise ProviderUnknownOutcomeError(
                    "The publishing outcome is unknown; check status before retrying."
                ) from exc
            raise ProviderTemporaryFailureError(
                "The publishing service is temporarily unavailable."
            ) from exc

        if response.status_code not in allowed_statuses:
            self.raise_for_response(response)
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            if unknown_on_transport_failure:
                raise ProviderUnknownOutcomeError(
                    "The publishing outcome is unknown; check status before retrying."
                ) from exc
            raise ProviderTemporaryFailureError(
                "The publishing service returned an invalid response."
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderTemporaryFailureError(
                "The publishing service returned an invalid response."
            )
        return response, payload

    @staticmethod
    def raise_for_response(response):
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = {}
        error_code = str(payload.get("error_code") or payload.get("code") or "")
        safe_details = {
            "status_code": response.status_code,
            "error_code": error_code,
        }
        if response.status_code in {400, 422}:
            raise ProviderValidationError(
                "The publishing service rejected the request.",
                safe_details=safe_details,
            )
        if response.status_code == 401:
            raise ProviderAuthenticationError(
                "Publishing credentials were rejected.",
                safe_details=safe_details,
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
            retry_after_seconds = None
            if str(retry_after).isdigit():
                retry_after_seconds = int(retry_after)
            elif retry_after:
                try:
                    retry_at = parsedate_to_datetime(str(retry_after))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    retry_after_seconds = max(
                        0,
                        int((retry_at - datetime.now(timezone.utc)).total_seconds()),
                    )
                except (TypeError, ValueError, OverflowError):
                    retry_after_seconds = None
            raise ProviderRateLimitError(
                "Publishing is temporarily rate limited.",
                safe_details=safe_details,
                retry_after_seconds=retry_after_seconds,
            )
        if response.status_code >= 500:
            raise ProviderTemporaryFailureError(
                "The publishing service is temporarily unavailable.",
                safe_details=safe_details,
            )
        raise ProviderPermanentFailureError(
            "The publishing request could not be completed.",
            safe_details=safe_details,
        )
