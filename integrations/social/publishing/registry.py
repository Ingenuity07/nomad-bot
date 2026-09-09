from collections.abc import Callable

from .adapters import UploadPostProvider, ZernioProvider
from .contract import PublishingProvider
from .errors import ProviderConfigurationError
from .types import ProviderName


ProviderFactory = Callable[..., PublishingProvider]


class PublishingProviderRegistry:
    def __init__(self):
        self._factories: dict[ProviderName, ProviderFactory] = {}

    def register(self, provider: ProviderName, factory: ProviderFactory, *, replace=False):
        provider = ProviderName(provider)
        if provider in self._factories and not replace:
            raise ProviderConfigurationError(
                f"Provider {provider.value} is already registered.",
                safe_details={"provider": provider.value},
            )
        self._factories[provider] = factory

    def create(self, provider: ProviderName | str, **kwargs):
        try:
            provider_name = ProviderName(provider)
        except ValueError as exc:
            raise ProviderConfigurationError("Unknown publishing provider.") from exc
        factory = self._factories.get(provider_name)
        if factory is None:
            raise ProviderConfigurationError(
                f"Provider {provider_name.value} is not registered.",
                safe_details={"provider": provider_name.value},
            )
        instance = factory(**kwargs)
        if not isinstance(instance, PublishingProvider):
            raise ProviderConfigurationError(
                f"Provider factory for {provider_name.value} returned an invalid adapter.",
                safe_details={"provider": provider_name.value},
            )
        return instance

    def registered_providers(self):
        return tuple(self._factories)


publishing_provider_registry = PublishingProviderRegistry()
publishing_provider_registry.register(ProviderName.UPLOAD_POST, UploadPostProvider)
publishing_provider_registry.register(ProviderName.ZERNIO, ZernioProvider)
