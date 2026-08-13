from abc import ABC, abstractmethod
from prospecting.discovery.dto import DiscoveryRequest, DiscoveryResult

class BusinessDiscoveryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable unique identifier name of the provider."""
        pass

    @abstractmethod
    def search(self, request: DiscoveryRequest) -> DiscoveryResult:
        """Execute the discovery search and return normalized results."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the provider is healthy and has credentials."""
        pass

    @abstractmethod
    def capabilities(self) -> list:
        """Return a list of features supported by the provider."""
        pass

    @abstractmethod
    def estimate_cost(self, request: DiscoveryRequest) -> float:
        """Return estimated monetary cost of executing this request."""
        pass
