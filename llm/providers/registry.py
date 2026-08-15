from typing import Dict, Any, List

class ProviderRegistry:
    """Manages external integration providers, status, and fallbacks."""
    
    def __init__(self):
        self._providers: Dict[str, Any] = {}
        self._enabled: Dict[str, bool] = {}

    def register(self, name: str, provider: Any, enabled: bool = True):
        self._providers[name] = provider
        self._enabled[name] = enabled

    def get(self, name: str) -> Any:
        if name in self._providers:
            return self._providers[name]
        try:
            from prospecting.discovery.providers.registry import discovery_provider_registry
            if discovery_provider_registry.has(name):
                return discovery_provider_registry.get(name)
        except (ImportError, KeyError):
            pass
        raise KeyError(f"Provider '{name}' not found in registry.")

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def enable(self, name: str):
        if name in self._providers:
            self._enabled[name] = True

    def disable(self, name: str):
        if name in self._providers:
            self._enabled[name] = False

    def list_all(self) -> List[str]:
        return list(self._providers.keys())

provider_registry = ProviderRegistry()
