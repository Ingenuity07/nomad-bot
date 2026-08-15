import logging
import requests
import urllib.parse
from typing import List, Optional
from llm.providers.base import CompanyDiscoveryProvider, CompanyCandidate
from llm.providers.registry import provider_registry

logger = logging.getLogger(__name__)

class OSMCompanyDiscoveryProvider(CompanyDiscoveryProvider):
    """OpenStreetMap Nominatim provider for structural local company searches."""

    def search_companies(self, query: str, geography: Optional[str] = None, limit: int = 20) -> List[CompanyCandidate]:
        candidates: List[CompanyCandidate] = []
        query_str = f"{query} in {geography}" if geography else query
        osm_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query_str)}&format=json&addressdetails=1"
        headers = {"User-Agent": "NomadProspectingEngine/1.0 (contact: support@visiofytech.com)"}

        try:
            logger.info(f"OSM query search: {query_str}")
            res = requests.get(osm_url, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                for item in data[:limit]:
                    name = item.get("display_name", "").split(",")[0].strip()
                    address = item.get("display_name", "")
                    category = item.get("type", "Business")
                    place_id = str(item.get("place_id", ""))
                    
                    if name:
                        candidates.append(
                            CompanyCandidate(
                                name=name,
                                address=address,
                                category=category,
                                source="openstreetmap",
                                external_id=place_id,
                                raw_metadata=item
                            )
                        )
        except Exception as e:
            logger.error(f"OSM Nominatim API request failed: {e}")
            
        return candidates

# Auto-register to provider registry
provider_registry.register("openstreetmap", OSMCompanyDiscoveryProvider())
