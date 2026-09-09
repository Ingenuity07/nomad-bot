from typing import Any, Mapping

from .types import ProviderErrorCategory, ProviderErrorInfo, safe_metadata_mapping


class PublishingProviderError(RuntimeError):
    category = ProviderErrorCategory.PERMANENT_FAILURE

    def __init__(
        self,
        message: str,
        *,
        safe_details: Mapping[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.safe_details = safe_metadata_mapping(safe_details)
        self.retry_after_seconds = retry_after_seconds

    def as_info(self):
        return ProviderErrorInfo(
            category=self.category,
            message=str(self),
            safe_details=self.safe_details,
            retry_after_seconds=self.retry_after_seconds,
        )


class ProviderConfigurationError(PublishingProviderError):
    category = ProviderErrorCategory.CONFIGURATION


class ProviderAuthenticationError(PublishingProviderError):
    category = ProviderErrorCategory.AUTHENTICATION


class ProviderValidationError(PublishingProviderError):
    category = ProviderErrorCategory.VALIDATION


class ProviderRateLimitError(PublishingProviderError):
    category = ProviderErrorCategory.RATE_LIMIT


class ProviderTemporaryFailureError(PublishingProviderError):
    category = ProviderErrorCategory.TEMPORARY_FAILURE


class ProviderPermanentFailureError(PublishingProviderError):
    category = ProviderErrorCategory.PERMANENT_FAILURE


class ProviderUnknownOutcomeError(PublishingProviderError):
    category = ProviderErrorCategory.UNKNOWN_OUTCOME
