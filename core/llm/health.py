import time
from typing import Dict

class ProviderHealthMonitor:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ProviderHealthMonitor, cls).__new__(cls, *args, **kwargs)
            cls._instance.health_status = {}
        return cls._instance

    def report_failure(self, provider_name: str, status_code: int = 500, blacklist_duration: int = 120):
        """
        Record a failure for the provider.
        If a rate limit (429) or server error occurs, blacklist the provider for a duration (default 2 mins).
        """
        now = time.time()
        self.health_status[provider_name] = {
            "healthy": False,
            "status_code": status_code,
            "cooldown_until": now + blacklist_duration,
            "failed_at": now
        }

    def report_success(self, provider_name: str):
        """Mark a provider as healthy."""
        self.health_status[provider_name] = {
            "healthy": True,
            "cooldown_until": 0,
            "failed_at": 0
        }

    def is_healthy(self, provider_name: str) -> bool:
        """Verify if the provider is healthy or has passed its cooldown period."""
        status = self.health_status.get(provider_name)
        if not status:
            return True
        if status.get("healthy"):
            return True
        # If in cooldown, check if time has elapsed
        if time.time() > status.get("cooldown_until", 0):
            self.report_success(provider_name)
            return True
        return False
