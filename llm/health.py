import time
from typing import Dict

class ProviderHealthMonitor:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ProviderHealthMonitor, cls).__new__(cls, *args, **kwargs)
            cls._instance.health_status = {}
        return cls._instance

    def _get_key(self, provider_name: str, model_name: str = None) -> str:
        return f"{provider_name}:{model_name}" if model_name else provider_name

    def report_failure(self, provider_name: str, model_name: str = None, status_code: int = 500, blacklist_duration: int = 120):
        """
        Record a failure for the provider/model.
        If a rate limit (429) or server error occurs, blacklist it for a duration (default 2 mins).
        """
        key = self._get_key(provider_name, model_name)
        now = time.time()
        self.health_status[key] = {
            "healthy": False,
            "status_code": status_code,
            "cooldown_until": now + blacklist_duration,
            "failed_at": now
        }

    def report_success(self, provider_name: str, model_name: str = None):
        """Mark a provider/model as healthy."""
        key = self._get_key(provider_name, model_name)
        self.health_status[key] = {
            "healthy": True,
            "cooldown_until": 0,
            "failed_at": 0
        }

    def is_healthy(self, provider_name: str, model_name: str = None) -> bool:
        """Verify if the provider/model is healthy or has passed its cooldown period."""
        if model_name:
            # If the entire provider is unhealthy, the model is unhealthy
            if not self.is_healthy(provider_name):
                return False
        
        key = self._get_key(provider_name, model_name)
        status = self.health_status.get(key)
        if not status:
            return True
        if status.get("healthy"):
            return True
        # If in cooldown, check if time has elapsed
        if time.time() > status.get("cooldown_until", 0):
            self.report_success(provider_name, model_name)
            return True
        return False

    def reset(self, provider_name: str = None):
        """Reset health status for a specific provider/model or all."""
        if provider_name:
            # Remove provider key and any model keys associated with it
            keys_to_remove = [k for k in self.health_status.keys() if k == provider_name or k.startswith(f"{provider_name}:")]
            for k in keys_to_remove:
                self.health_status.pop(k, None)
        else:
            self.health_status.clear()
