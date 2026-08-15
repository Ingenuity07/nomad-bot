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
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            res = requests.get(ddg_url, headers=headers, timeout=8)
            logger.info(f"DuckDuckGo search HTTP response: {res.status_code} for query: {query}")
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.find_all("a", class_="result__a")
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
                            "snippet": link.parent.find_next_sibling("div", class_="result__snippet").text.strip() if link.parent else ""
                        })
        except Exception as e:
            logger.error(f"DuckDuckGo search query '{query}' failed: {e}")
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

# Auto-register to provider registry
provider_registry.register("duckduckgo", DuckDuckGoSearchProvider())
