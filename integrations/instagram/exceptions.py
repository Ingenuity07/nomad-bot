"""
Domain exceptions for Instagram integration.
"""


class InstagramIntegrationError(Exception):
    """Base exception for all Instagram integration errors."""
    pass


class InstagramOAuthError(InstagramIntegrationError):
    """Raised when an error occurs during OAuth correlation or token exchange."""
    pass


class InstagramWebhookError(InstagramIntegrationError):
    """Raised when an error occurs during webhook ingestion or verification."""
    pass


class InstagramAutomationError(InstagramIntegrationError):
    """Raised when an automation rule evaluation or action execution fails."""
    pass


class InstagramSecurityError(InstagramIntegrationError):
    """Raised for token encryption, decryption, or signature verification errors."""
    pass
