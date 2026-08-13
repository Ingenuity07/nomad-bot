import logging
import requests
import urllib.parse
from bs4 import BeautifulSoup
from abc import ABC, abstractmethod
from prospecting.models import LeadCompany

logger = logging.getLogger(__name__)

class SocialSignalProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def fetch_signals(self, company: LeadCompany) -> list:
        """
        Queries external channels and returns a list of dictionaries representing detected signals:
        {
            "signal_name": str,
            "category": str,
            "description": str,
            "source_url": str,
            "evidence_text": str,
            "confidence": float
        }
        """
        pass


class SearchSignalProvider(SocialSignalProvider):
    @property
    def name(self) -> str:
        return "search_signal_provider"

    def fetch_signals(self, company: LeadCompany) -> list:
        detected = []
        name = company.name
        
        # Query: look for jobs pages on indeed or linkedin
        query = f'site:linkedin.com/jobs "{name}" OR site:indeed.com "{name}"'
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            logger.info(f"Querying external signals for '{name}': {query}")
            res = requests.get(ddg_url, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                links = soup.find_all("a", class_="result__a")
                
                for link in links[:3]:
                    title = link.text.strip()
                    raw_href = link.get("href", "")
                    href = self._extract_real_url(raw_href)
                    
                    if not href:
                        continue
                    
                    if "linkedin.com" in href.lower() or "indeed.com" in href.lower():
                        detected.append({
                            "signal_name": "Active Recruitment Detected",
                            "category": "HIRING",
                            "description": f"Found external job listings on search engine index: {title}",
                            "source_url": href,
                            "evidence_text": f"Scraped job search index match: {title}",
                            "confidence": 0.85
                        })
        except Exception as e:
            logger.error(f"Failed to fetch external signals for '{name}': {e}")
            
        return detected

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
        return href
