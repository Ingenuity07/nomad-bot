from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from prospecting.exceptions import NormalizationError

@dataclass
class DiscoveryRequest:
    query: str
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[int] = None
    limit: int = 20
    filters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self):
        if not self.query.strip():
            raise NormalizationError("Discovery query cannot be empty.")
        if not self.location.strip():
            raise NormalizationError("Discovery location cannot be empty.")
        if self.limit <= 0:
            raise NormalizationError("Limit parameter must be greater than zero.")
        if self.latitude is not None and not (-90.0 <= self.latitude <= 90.0):
            raise NormalizationError("Latitude must be between -90.0 and 90.0.")
        if self.longitude is not None and not (-180.0 <= self.longitude <= 180.0):
            raise NormalizationError("Longitude must be between -180.0 and 180.0.")
        if self.radius_meters is not None and not (0 < self.radius_meters <= 50000):
            raise NormalizationError("Radius must be between 1 and 50000 meters.")


@dataclass
class DiscoveryResultItem:
    name: str
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    category: Optional[str] = None
    rating: float = 0.0
    external_id: Optional[str] = None
    raw_reference: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryResult:
    provider: str
    request_id: str
    results: List[DiscoveryResultItem] = field(default_factory=list)
    next_page_token: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
