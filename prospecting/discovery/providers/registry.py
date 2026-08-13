from typing import Dict, List
from prospecting.discovery.providers.base import BusinessDiscoveryProvider

class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, BusinessDiscoveryProvider] = {}

    def register(self, provider: BusinessDiscoveryProvider):
        self._providers[provider.name] = provider

    def get(self, name: str) -> BusinessDiscoveryProvider:
        if name not in self._providers:
            raise KeyError(f"Provider '{name}' not found in registry.")
        return self._providers[name]

    def list_all(self) -> List[BusinessDiscoveryProvider]:
        return list(self._providers.values())

    def has(self, name: str) -> bool:
        return name in self._providers

# Global discovery provider registry instance
discovery_provider_registry = ProviderRegistry()
