import logging
import requests
import urllib.parse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from prospecting.models import DiscoveryRun, LeadCompany
from llm.tools.implementations.browser_tool import BrowserTool

logger = logging.getLogger(__name__)

class BusinessDiscoveryEngine:
    """Discovers target businesses in a location using a parallel 5-way search aggregator."""

    def __init__(self):
        self.browser_tool = BrowserTool()

    def discover_businesses(self, discovery_run: DiscoveryRun) -> list:
        keyword = discovery_run.keyword
        location = discovery_run.location
        
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

        # 4. Deduplicate by Name (Case-insensitive, stripped) and Website
        unique_companies = []
        seen_names = set()
        seen_websites = set()

        for c in companies:
            norm_name = c["name"].lower().strip()
            norm_web = c["website"].lower().strip() if c["website"] else ""
            
            # Avoid duplicating results
            if norm_name not in seen_names and (not norm_web or norm_web not in seen_websites):
                seen_names.add(norm_name)
                if norm_web:
                    seen_websites.add(norm_web)
                unique_companies.append(c)

        # 5. Resolve missing website URLs and persist to CRM database
        for company in unique_companies:
            if not company["website"]:
                company["website"] = self.resolve_website_url(company["name"], location)

            LeadCompany.objects.create(
                discovery_run=discovery_run,
                name=company["name"][:255] if company["name"] else "Unknown",
                website=company["website"][:2000] if company["website"] else None,
                phone=company["phone"][:100] if company["phone"] else None,
                address=company["address"] or None,
                category=company["category"][:100] if company["category"] else None
            )

        return unique_companies

    def _query_openstreetmap(self, keyword: str, location: str) -> list:
        """Query Nominatim OpenStreetMap directory."""
        companies = []
        query_str = f"{keyword} in {location}"
        osm_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query_str)}&format=json&addressdetails=1"
        headers = {"User-Agent": "NomadProspectingEngine/1.0 (contact: support@visiofytech.com)"}

        try:
            logger.info(f"Querying OpenStreetMap for: {query_str}")
            res = requests.get(osm_url, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                for item in data[:8]:
                    name = item.get("display_name", "").split(",")[0].strip()
                    address = item.get("display_name", "")
                    category = item.get("type", "Business")
                    if name:
                        companies.append({
                            "name": name,
                            "address": address,
                            "category": category,
                            "phone": "",
                            "website": ""
                        })
        except Exception as e:
            logger.error(f"OSM Nominatim API request failed: {e}")
        return companies

    def _search_duckduckgo(self, query: str, label: str, location: str) -> list:
        """Search DuckDuckGo HTML and return list of formatted business payloads."""
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
                            "website": href
                        })
        except Exception as e:
            logger.error(f"DuckDuckGo query '{query}' failed: {e}")
        return leads

    def _extract_real_url(self, href: str) -> str:
        """Extract the real target URL from DuckDuckGo redirect query parameter 'uddg'."""
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

    def resolve_website_url(self, business_name: str, location: str) -> str:
        """Search DuckDuckGo for the business official website."""
        query = f"{business_name} {location} official website"
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            res = requests.get(ddg_url, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.find_all("a", class_="result__a")
                for link in links[:3]:
                    raw_href = link.get("href", "")
                    url = self._extract_real_url(raw_href)
                    if url and not any(x in url.lower() for x in ["duckduckgo", "wikipedia", "yelp", "tripadvisor", "yell.com"]):
                        return url
        except Exception as e:
            logger.error(f"Failed to resolve website link for {business_name}: {e}")

        return ""
