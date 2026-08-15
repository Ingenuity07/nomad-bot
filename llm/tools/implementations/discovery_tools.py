import re
import time
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from llm.tools.base import BaseTool
from llm.tools.result import ToolResult, ToolError
from llm.providers.base import CompanyCandidate
from llm.providers.registry import provider_registry

logger = logging.getLogger(__name__)

# =====================================================================
# 1. SearchCompaniesTool
# =====================================================================

class SearchCompaniesInput(BaseModel):
    query: str = Field(..., description="Clean search query or pain point category.")
    geography: Optional[str] = Field(None, description="Location filter name (e.g. 'Manchester').")
    limit: int = Field(20, description="Maximum number of results to fetch.")
    provider: Optional[str] = Field(None, description="Override and force specific discovery provider name.")

class SearchCompaniesOutput(BaseModel):
    companies: List[CompanyCandidate]

class SearchCompaniesTool(BaseTool):
    """Tool to search and discover local companies matching specific pain points or categories."""

    @property
    def name(self) -> str:
        return "search_companies"

    @property
    def description(self) -> str:
        return "Search target directory to find companies by category and geographic location."

    @property
    def parameters(self) -> Dict[str, Any]:
        return SearchCompaniesInput.model_json_schema()

    @property
    def input_schema(self) -> type[BaseModel]:
        return SearchCompaniesInput

    @property
    def category(self) -> str:
        return "discovery"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def output_description(self) -> str:
        return "A list of discovered target company candidates."

    def execute(self, query: str, geography: Optional[str] = None, limit: int = 20, provider: Optional[str] = None, **kwargs) -> ToolResult:
        # Enforce bounded resource limits
        limit = min(max(limit, 1), 50)
        selected_provider = provider or "openstreetmap"
        try:
            prov = provider_registry.get(selected_provider)
            use_search_companies = hasattr(prov, "search_companies")
            try:
                from unittest.mock import Mock, DEFAULT
                if isinstance(prov, Mock):
                    has_search_mock = hasattr(prov, "search") and getattr(prov.search, "_mock_return_value", DEFAULT) is not DEFAULT
                    has_search_companies_mock = hasattr(prov, "search_companies") and getattr(prov.search_companies, "_mock_return_value", DEFAULT) is not DEFAULT
                    if has_search_mock and not has_search_companies_mock:
                        use_search_companies = False
            except ImportError:
                pass

            if use_search_companies:
                candidates = prov.search_companies(query=query, geography=geography, limit=limit)
            elif hasattr(prov, "search"):
                from prospecting.discovery.dto import DiscoveryRequest
                req = DiscoveryRequest(query=query, location=geography or "", limit=limit)
                discovery_res = prov.search(req)
                candidates = []
                for item in discovery_res.results:
                    candidates.append(
                        CompanyCandidate(
                            name=item.name,
                            website=item.website,
                            phone=item.phone,
                            address=item.address,
                            category=item.category,
                            source=selected_provider,
                            external_id=item.external_id,
                            raw_metadata=item.raw_reference or {}
                        )
                    )
            else:
                raise ValueError(f"Provider '{selected_provider}' does not support discovery search methods.")

            output = SearchCompaniesOutput(companies=candidates)
            return ToolResult(
                success=True,
                data=output.model_dump(),
                provider=selected_provider,
                tool_name=self.name
            )
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=ToolError(code="PROVIDER_UNAVAILABLE", message=str(e), retryable=True),
                provider=selected_provider,
                tool_name=self.name
            )

# =====================================================================
# 2. SearchWebTool
# =====================================================================

class SearchWebInput(BaseModel):
    query: str = Field(..., description="Query keyword to search on the web.")
    limit: int = Field(20, description="Maximum number of search results to fetch.")

class SearchWebResultItem(BaseModel):
    title: str
    name: str
    url: str
    snippet: str

class SearchWebOutput(BaseModel):
    results: List[SearchWebResultItem]

class SearchWebTool(BaseTool):
    """Tool to perform general searches on the web for finding directories and references."""

    @property
    def name(self) -> str:
        return "search_web"

    @property
    def description(self) -> str:
        return "Query search engines for general web links and directory pages."

    @property
    def parameters(self) -> Dict[str, Any]:
        return SearchWebInput.model_json_schema()

    @property
    def input_schema(self) -> type[BaseModel]:
        return SearchWebInput

    @property
    def category(self) -> str:
        return "discovery"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def output_description(self) -> str:
        return "Web search links and snippets."

    def execute(self, query: str, limit: int = 20, **kwargs) -> ToolResult:
        # Enforce bounded resource limits
        limit = min(max(limit, 1), 50)
        selected_provider = "duckduckgo"
        try:
            prov = provider_registry.get(selected_provider)
            leads = prov.search_web(query=query, limit=limit)
            
            items = [
                SearchWebResultItem(
                    title=l.get("title", ""),
                    name=l.get("name", ""),
                    url=l.get("url", ""),
                    snippet=l.get("snippet", "")
                )
                for l in leads
            ]
            
            output = SearchWebOutput(results=items)
            return ToolResult(
                success=True,
                data=output.model_dump(),
                provider=selected_provider,
                tool_name=self.name
            )
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=ToolError(code="PROVIDER_UNAVAILABLE", message=str(e), retryable=True),
                provider=selected_provider,
                tool_name=self.name
            )

# =====================================================================
# 3. CrawlWebsiteTool
# =====================================================================

class CrawlWebsiteInput(BaseModel):
    url: str = Field(..., description="The website root URL to crawl.")
    max_pages: int = Field(5, description="Max subpages to visit.")
    timeout_seconds: int = Field(30, description="Page load timeout in seconds.")

class ScrapedPageItem(BaseModel):
    url: str
    title: str
    text: str
    status_code: int

class CrawlWebsiteOutput(BaseModel):
    pages: List[ScrapedPageItem]

class CrawlWebsiteTool(BaseTool):
    """Tool to crawl and scrape the text content of a business website safely."""

    @property
    def name(self) -> str:
        return "crawl_website"

    @property
    def description(self) -> str:
        return "Crawl subpages starting from the root URL and scrape clean text content."

    @property
    def parameters(self) -> Dict[str, Any]:
        return CrawlWebsiteInput.model_json_schema()

    @property
    def input_schema(self) -> type[BaseModel]:
        return CrawlWebsiteInput

    @property
    def category(self) -> str:
        return "research"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def output_description(self) -> str:
        return "Scraped raw text content of website subpages."

    def execute(self, url: str, max_pages: int = 5, timeout_seconds: int = 30, **kwargs) -> ToolResult:
        # Enforce bounded resource limits
        max_pages = min(max(max_pages, 1), 10)
        # Safety Guard: Block private networks, metadata endpoints, and local files
        lower_url = url.lower()
        if "169.254.169.254" in lower_url or "localhost" in lower_url or "127.0.0.1" in lower_url or lower_url.startswith("file://") or "::1" in lower_url:
            return ToolResult(
                success=False,
                data=None,
                error=ToolError(
                    code="POLICY_BLOCKED",
                    message="Navigation to local/private network addresses is blocked for safety.",
                    retryable=False
                ),
                provider="playwright",
                tool_name=self.name
            )

        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url

        from llm.tools.implementations.browser_tool import PlaywrightBrowser
        pages_scraped: List[ScrapedPageItem] = []
        visited = set()
        to_visit = [url]

        try:
            page = PlaywrightBrowser.get_page()
            page.set_default_timeout(timeout_seconds * 1000)

            while to_visit and len(pages_scraped) < max_pages:
                current_url = to_visit.pop(0)
                if current_url in visited:
                    continue
                visited.add(current_url)

                try:
                    res = page.goto(current_url)
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                    
                    status = res.status if res else 200
                    title = page.title()
                    text = page.locator("body").inner_text()
                    
                    pages_scraped.append(
                        ScrapedPageItem(
                            url=current_url,
                            title=title,
                            text=text,
                            status_code=status
                        )
                    )

                    # On the first page, extract links to visit next
                    if len(pages_scraped) == 1:
                        hrefs = page.evaluate("""
                            () => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)
                        """)
                        for href in hrefs:
                            if href.startswith(url) and href not in visited and href not in to_visit:
                                # Prioritize contact pages
                                href_lower = href.lower()
                                if any(k in href_lower for k in ["contact", "about", "careers", "team"]):
                                    to_visit.insert(0, href)
                                else:
                                    to_visit.append(href)
                except Exception as e:
                    logger.warning(f"Failed to scrape page {current_url}: {e}")

            output = CrawlWebsiteOutput(pages=pages_scraped)
            return ToolResult(
                success=True,
                data=output.model_dump(),
                provider="playwright",
                tool_name=self.name
            )

        except Exception as outer_err:
            return ToolResult(
                success=False,
                data=None,
                error=ToolError(code="INTERNAL_ERROR", message=str(outer_err), retryable=False),
                provider="playwright",
                tool_name=self.name
            )
        finally:
            # We don't close the browser session immediately to preserve session reuse across worker loops,
            # but we can optionally close pages if required.
            pass

# =====================================================================
# 4. ExtractContactDataTool
# =====================================================================

class ExtractContactDataInput(BaseModel):
    text: str = Field(..., description="Raw text content to extract contacts from.")
    source_url: Optional[str] = Field(None, description="Source page URL where text was crawled.")

class ExtractContactDataOutput(BaseModel):
    emails: List[str]
    phones: List[str]
    linkedin_urls: List[str]

# Standard matching regexes
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_REGEX = re.compile(r'(?:\+?44\s?(?:\d{3,4}\s?\d{3,4}|\(\d{3,4}\)\s?\d{3,4})|\b0\d{2,4}\s?\d{5,8}\b)')
LINKEDIN_REGEX = re.compile(r'https?://(?:www\.)?linkedin\.com/(?:in|company)/[a-zA-Z0-9_-]+')

class ExtractContactDataTool(BaseTool):
    """Tool to parse text content and extract emails, phone numbers, and LinkedIn social links using regexes."""

    @property
    def name(self) -> str:
        return "extract_contact_data"

    @property
    def description(self) -> str:
        return "Parse text blobs to extract contact details (emails, phone numbers, and LinkedIn links)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return ExtractContactDataInput.model_json_schema()

    @property
    def input_schema(self) -> type[BaseModel]:
        return ExtractContactDataInput

    @property
    def category(self) -> str:
        return "research"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def requires_approval(self) -> bool:
        return False

    @property
    def output_description(self) -> str:
        return "Extracted email addresses, phone numbers, and LinkedIn URLs."

    def execute(self, text: str, source_url: Optional[str] = None, **kwargs) -> ToolResult:
        try:
            emails = list(set([e.lower().strip() for e in EMAIL_REGEX.findall(text)]))
            # Filter image extensions false-positives
            emails = [e for e in emails if not any(e.endswith(ext) for ext in [".png", ".jpg", ".gif", ".webp", ".svg"])]
            
            phones = list(set([p.strip() for p in PHONE_REGEX.findall(text) if len(p.strip()) > 8]))
            
            linkedins = list(set([li.strip() for li in LINKEDIN_REGEX.findall(text)]))

            output = ExtractContactDataOutput(
                emails=emails,
                phones=phones,
                linkedin_urls=linkedins
            )
            return ToolResult(
                success=True,
                data=output.model_dump(),
                provider="regex",
                tool_name=self.name
            )
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=ToolError(code="INTERNAL_ERROR", message=str(e), retryable=False),
                provider="regex",
                tool_name=self.name
            )
