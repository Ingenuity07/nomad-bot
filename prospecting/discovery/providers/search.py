import logging
import requests
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from prospecting.exceptions import DiscoveryError
from prospecting.discovery.dto import DiscoveryRequest, DiscoveryResult, DiscoveryResultItem
from prospecting.discovery.providers.base import BusinessDiscoveryProvider
from prospecting.discovery.providers.registry import discovery_provider_registry

logger = logging.getLogger(__name__)

class SearchProvider(BusinessDiscoveryProvider):
    @property
    def name(self) -> str:
        return "search"

    def health_check(self) -> bool:
        # Default local queries do not require any configuration keys
        return True

    def capabilities(self) -> list:
        return ["openstreetmap", "duckduckgo", "parallel_scraping"]

    def estimate_cost(self, request: DiscoveryRequest) -> float:
        # Free queries
        return 0.0

    def search(self, request: DiscoveryRequest) -> DiscoveryResult:
        request.validate()
        keyword = request.query
        location = request.location
        limit = min(request.limit, 20)

        # 1. Structured OpenStreetMap query
        companies = self._query_openstreetmap(keyword, location)
        
        # 2. Define parallel search queries
        search_queries = {
            "direct": f"{keyword} in {location}",
            "contact": f'"{keyword}" "{location}" contact email',
            "directory": f"top {keyword} companies in {location}",
            "reddit": f'site:reddit.com "{location}" "{keyword}" "route"',
            "github": f'site:github.com "{keyword}" OR "logistics" location:{location}'
        }

        # 3. Execute DuckDuckGo searches in parallel
        web_leads = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._search_duckduckgo, query, label, location): label 
                for label, query in search_queries.items()
            }
            
            for future in as_completed(futures):
                label = futures[future]
                try:
                    results = future.result()
                    logger.info(f"Search '{label}' yielded {len(results)} raw links.")
                    web_leads.extend(results)
                except Exception as e:
                    logger.error(f"Search '{label}' failed: {e}")

        # Combine OSM and Web Leads
        companies.extend(web_leads)

        # Limit combined items to request limit to maintain budget boundaries
        companies = companies[:limit]

        # 4. Map to DTO result items
        result_items: List[DiscoveryResultItem] = []
        for c in companies:
            result_items.append(
                DiscoveryResultItem(
                    name=c["name"],
                    website=c["website"] or None,
                    phone=c["phone"] or None,
                    address=c["address"] or None,
                    category=c["category"] or None,
                    rating=0.0,
                    external_id=c.get("external_id"),
                    raw_reference=c
                )
            )

        return DiscoveryResult(
            provider=self.name,
            request_id=f"search-req-{int(time.time())}" if 'time' in globals() else "search-req",
            results=result_items,
            next_page_token=None,
            usage={"queried_count": len(result_items), "estimated_cost": 0.0},
            raw_metadata={"combined_leads_count": len(companies)}
        )

    def _query_openstreetmap(self, keyword: str, location: str) -> list:
        companies = []
        query_str = f"{keyword} in {location}"
        osm_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query_str)}&format=json&addressdetails=1"
        headers = {"User-Agent": "NomadProspectingEngine/1.0 (contact: support@visiofytech.com)"}

        try:
            logger.info(f"OSM query: {query_str}")
            res = requests.get(osm_url, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                for item in data[:8]:
                    name = item.get("display_name", "").split(",")[0].strip()
                    address = item.get("display_name", "")
                    category = item.get("type", "Business")
                    place_id = str(item.get("place_id", ""))
                    if name:
                        companies.append({
                            "name": name,
                            "address": address,
                            "category": category,
                            "phone": "",
                            "website": "",
                            "external_id": place_id
                        })
        except Exception as e:
            logger.error(f"OSM Nominatim API request failed: {e}")
        return companies

    def _search_duckduckgo(self, query: str, label: str, location: str) -> list:
        leads = []
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            res = requests.get(ddg_url, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.find_all("a", class_="result__a")
                for link in links[:5]:
                    title = link.text.strip()
                    raw_href = link.get("href", "")
                    href = self._extract_real_url(raw_href)

                    if not href:
                        continue

                    # Determine Lead Category
                    category = "Courier Service"
                    if "reddit.com" in href.lower():
                        category = "Reddit Intent"
                    elif "github.com" in href.lower():
                        category = "GitHub Tech"
                    elif label == "directory":
                        category = "Directory Listing"

                    # Filter out common search engines / portals
                    if not any(x in href.lower() for x in ["duckduckgo.com", "google.com", "bing.com"]):
                        leads.append({
                            "name": title.split("-")[0].split("|")[0].strip(),
                            "address": f"{location}, UK",
                            "category": category,
                            "phone": "",
                            "website": href,
                            "external_id": href
                        })
        except Exception as e:
            logger.error(f"DuckDuckGo query '{query}' failed: {e}")
        return leads

    def _extract_real_url(self, href: str) -> str:
        if not href:
            return ""
        if "uddg=" in href:
            try:
                parsed_url = urllib.parse.urlparse(href)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                real_url = query_params.get("uddg", [None])[0]
                if real_url:
                    return real_url
            except Exception:
                pass
        if href.startswith("//"):
            return "https:" + href
        return href

import time
# Auto-register to registry on module load
discovery_provider_registry.register(SearchProvider())
