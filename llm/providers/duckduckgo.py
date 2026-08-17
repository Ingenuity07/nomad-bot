import logging
import requests
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from llm.providers.base import WebSearchProvider
from llm.providers.registry import provider_registry

logger = logging.getLogger(__name__)

class DuckDuckGoSearchProvider(WebSearchProvider):
    """DuckDuckGo provider for performing web search scrapes."""

    def search_web(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        leads = []
        ddg_url = "https://html.duckduckgo.com/html/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            logger.info("PROVIDER_REQUEST provider=duckduckgo query=%r limit=%s", query, limit)
            # POST is the form used by DDG's HTML endpoint and is less brittle than
            # embedding long prospecting queries in the URL.
            res = requests.post(ddg_url, data={"q": query}, headers=headers, timeout=12)
            logger.info(f"DuckDuckGo search HTTP response: {res.status_code} for query: {query}")
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.select("a.result__a, a.result-link")
                for link in links[:limit]:
                    title = link.text.strip()
                    raw_href = link.get("href", "")
                    href = self._extract_real_url(raw_href)

                    if not href:
                        continue

                    # Filter out search engines
                    if not any(x in href.lower() for x in ["duckduckgo.com", "google.com", "bing.com"]):
                        leads.append({
                            "title": title,
                            "name": title.split("-")[0].split("|")[0].strip(),
                            "url": href,
                            "snippet": self._extract_snippet(link)
                        })
                logger.info("PROVIDER_RESPONSE provider=duckduckgo query=%r result_count=%s results=%s", query, len(leads), leads)
            else:
                logger.warning("PROVIDER_RESPONSE provider=duckduckgo query=%r status=%s body_preview=%r", query, res.status_code, res.text[:500])
        except Exception as e:
            logger.error(f"DuckDuckGo search query '{query}' failed: {e}")
        return leads

    @staticmethod
    def _extract_snippet(link) -> str:
        result = link.find_parent(class_="result")
        snippet = result.select_one(".result__snippet") if result else None
        return snippet.get_text(" ", strip=True) if snippet else ""

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

# Auto-register to provider registry
provider_registry.register("duckduckgo", DuckDuckGoSearchProvider())
