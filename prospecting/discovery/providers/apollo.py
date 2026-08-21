import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from prospecting.discovery.dto import DiscoveryRequest, DiscoveryResult, DiscoveryResultItem
from prospecting.discovery.providers.base import BusinessDiscoveryProvider
from prospecting.discovery.providers.config import env_flag, env_value
from prospecting.discovery.providers.registry import discovery_provider_registry
from prospecting.exceptions import DiscoveryError

logger = logging.getLogger(__name__)


class ApolloProvider(BusinessDiscoveryProvider):
    """Discover B2B organizations through Apollo's Organization Search API."""

    @property
    def name(self) -> str:
        return "apollo"

    def health_check(self) -> bool:
        try:
            return env_flag("APOLLO_ENABLED") and bool(env_value("APOLLO_API_KEY"))
        except ValueError as error:
            logger.warning("Apollo provider configuration is invalid: %s", error)
            return False

    def capabilities(self) -> list:
        return [
            "organization_search",
            "location_filtering",
            "employee_filtering",
            "technology_filtering",
        ]

    def estimate_cost(self, request: DiscoveryRequest) -> float:
        # Apollo bills this endpoint in plan credits, not at a universal USD rate.
        return 0.0

    def search(self, request: DiscoveryRequest) -> DiscoveryResult:
        request.validate()

        try:
            enabled = env_flag("APOLLO_ENABLED")
        except ValueError as error:
            raise DiscoveryError(str(error)) from error
        if not enabled:
            raise DiscoveryError("Apollo provider is disabled by APOLLO_ENABLED.")

        api_key = env_value("APOLLO_API_KEY")
        if not api_key:
            raise DiscoveryError("Apollo API key is not configured.")

        url = os.environ.get(
            "APOLLO_ORGANIZATION_SEARCH_URL",
            "https://api.apollo.io/api/v1/mixed_companies/search",
        ).strip()
        timeout = self._positive_int_env("APOLLO_TIMEOUT_SECONDS", 15)
        per_page = min(request.limit, 100)
        params = self._build_params(request, per_page)
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        }

        response = None
        for attempt in range(3):
            try:
                logger.info(
                    "PROVIDER_REQUEST provider=apollo query=%r location=%r limit=%s attempt=%s",
                    request.query,
                    request.location,
                    per_page,
                    attempt + 1,
                )
                response = requests.post(url, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as error:
                if attempt == 2:
                    raise DiscoveryError(f"Apollo API request failed: {error}") from error
                time.sleep(2**attempt)
                continue

            if response.status_code == 200:
                break
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(max(float(retry_after), 0.0), 10.0)
                    except (TypeError, ValueError):
                        delay = float(2**attempt)
                    time.sleep(delay)
                    continue
            raise DiscoveryError(self._response_error(response))

        if response is None or response.status_code != 200:
            raise DiscoveryError("Apollo API did not return a successful response after retries.")

        try:
            payload = response.json()
        except ValueError as error:
            raise DiscoveryError("Apollo API returned invalid JSON.") from error

        organizations = payload.get("organizations") or payload.get("accounts") or []
        if not isinstance(organizations, list):
            raise DiscoveryError("Apollo API response did not contain an organization list.")

        results = [self._normalize_organization(item) for item in organizations[:per_page]]
        results = [item for item in results if item.name]
        pagination = payload.get("pagination") or {}

        logger.info(
            "PROVIDER_RESPONSE provider=apollo query=%r result_count=%s",
            request.query,
            len(results),
        )
        return DiscoveryResult(
            provider=self.name,
            request_id=str(pagination.get("page") or "1"),
            results=results,
            next_page_token=self._next_page_token(pagination),
            usage={
                "organization_count": len(results),
                "credits_estimated": 1,
                "estimated_cost": self.estimate_cost(request),
            },
            raw_metadata={"pagination": pagination},
        )

    @staticmethod
    def _build_params(request: DiscoveryRequest, per_page: int) -> List[Tuple[str, Any]]:
        params: List[Tuple[str, Any]] = [
            ("q_organization_keyword_tags[]", request.query),
            ("organization_locations[]", request.location),
            ("page", 1),
            ("per_page", per_page),
        ]
        filters = request.filters or {}
        filter_mappings = {
            "employee_ranges": "organization_num_employees_ranges[]",
            "technologies": "currently_using_any_of_technology_uids[]",
            "domains": "q_organization_domains_list[]",
        }
        for filter_name, api_name in filter_mappings.items():
            values = filters.get(filter_name, [])
            if isinstance(values, str):
                values = [values]
            for value in values:
                if str(value).strip():
                    params.append((api_name, str(value).strip()))
        return params

    @staticmethod
    def _normalize_organization(item: Dict[str, Any]) -> DiscoveryResultItem:
        domain = item.get("primary_domain") or item.get("domain")
        website = item.get("website_url") or item.get("website")
        if not website and domain:
            website = f"https://{domain}"

        primary_phone = item.get("primary_phone") or {}
        if isinstance(primary_phone, dict):
            phone = primary_phone.get("number") or primary_phone.get("sanitized_number")
        else:
            phone = primary_phone
        phone = phone or item.get("phone")

        address = item.get("raw_address") or item.get("address")
        if not address:
            address_parts = [
                item.get("street_address"),
                item.get("city"),
                item.get("state"),
                item.get("postal_code"),
                item.get("country"),
            ]
            address = ", ".join(str(part) for part in address_parts if part)

        return DiscoveryResultItem(
            name=str(item.get("name") or "").strip(),
            website=website,
            phone=phone,
            address=address or None,
            category=item.get("industry") or item.get("industry_tag"),
            external_id=str(item.get("id") or item.get("organization_id") or "") or None,
            raw_reference=item,
        )

    @staticmethod
    def _next_page_token(pagination: Dict[str, Any]) -> Optional[str]:
        current_page = pagination.get("page")
        total_pages = pagination.get("total_pages")
        if isinstance(current_page, int) and isinstance(total_pages, int) and current_page < total_pages:
            return str(current_page + 1)
        return None

    @staticmethod
    def _response_error(response: requests.Response) -> str:
        messages = {
            401: "Apollo rejected the API key.",
            403: "Apollo denied access; check the API key scope and Apollo plan.",
            429: "Apollo rate limit was exceeded.",
        }
        return messages.get(
            response.status_code,
            f"Apollo API returned HTTP {response.status_code}.",
        )

    @staticmethod
    def _positive_int_env(name: str, default: int) -> int:
        raw_value = os.environ.get(name, str(default)).strip()
        try:
            value = int(raw_value)
        except ValueError as error:
            raise DiscoveryError(f"{name} must be an integer.") from error
        if value <= 0:
            raise DiscoveryError(f"{name} must be greater than zero.")
        return value


discovery_provider_registry.register(ApolloProvider())
