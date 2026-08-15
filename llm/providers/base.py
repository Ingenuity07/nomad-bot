from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, Any, List, Protocol

class CompanyCandidate(BaseModel):
    name: str
    domain: Optional[str] = None
    website: Optional[str] = None  # Use str to easily handle varying raw strings
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    source: str
    source_url: Optional[str] = None
    external_id: Optional[str] = None
    raw_metadata: Dict[str, Any] = {}

class CompanyDiscoveryProvider(Protocol):
    def search_companies(self, query: str, geography: Optional[str] = None, limit: int = 20) -> List[CompanyCandidate]:
        """Discover matching companies for a query and geography."""
        ...

class WebSearchProvider(Protocol):
    def search_web(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Perform generic search on the web and return titles and URLs."""
        ...

class WebsitePage(BaseModel):
    url: str
    title: str
    text: str
    status_code: int

class WebsiteProvider(Protocol):
    def crawl(self, url: str, max_pages: int = 5, timeout: int = 30) -> List[WebsitePage]:
        """Crawl website pages starting from the root URL."""
        ...
