import os
import time
import logging
import requests
from typing import List
from prospecting.exceptions import DiscoveryError
from prospecting.discovery.dto import DiscoveryRequest, DiscoveryResult, DiscoveryResultItem
from prospecting.discovery.providers.base import BusinessDiscoveryProvider
from prospecting.discovery.providers.config import env_flag, env_value
from prospecting.discovery.providers.registry import discovery_provider_registry

logger = logging.getLogger(__name__)

class GooglePlacesProvider(BusinessDiscoveryProvider):
    @property
    def name(self) -> str:
        return "google_places"

    def health_check(self) -> bool:
        try:
            return env_flag("GOOGLE_PLACES_ENABLED") and bool(
                env_value("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY")
            )
        except ValueError as error:
            logger.warning("Google Places provider configuration is invalid: %s", error)
            return False

    def capabilities(self) -> list:
        return ["text_search", "nearby_search", "field_masking"]

    def estimate_cost(self, request: DiscoveryRequest) -> float:
        # Google Places Text Search (New) with basic field mask is $0.025 per request
        return 0.025

    def search(self, request: DiscoveryRequest) -> DiscoveryResult:
        request.validate()
        try:
            enabled = env_flag("GOOGLE_PLACES_ENABLED")
        except ValueError as error:
            raise DiscoveryError(str(error)) from error
        if not enabled:
            raise DiscoveryError("Google Places provider is disabled by GOOGLE_PLACES_ENABLED.")

        api_key = env_value("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise DiscoveryError("Google Places API key is not configured.")

        url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.primaryType,places.rating,places.websiteUri"
        }
        
        query_str = f"{request.query} in {request.location}"
        payload = {
            "textQuery": query_str,
            "pageSize": min(request.limit, 20)
        }

        # Handle optional location bias
        if request.latitude is not None and request.longitude is not None:
            payload["locationBias"] = {
                "circle": {
                    "center": {
                        "latitude": request.latitude,
                        "longitude": request.longitude
                    },
                    "radius": float(request.radius_meters or 5000)
                }
            }

        retries = 3
        backoff = 1.0
        response = None

        for attempt in range(retries):
            try:
                logger.info("PROVIDER_REQUEST provider=google_places query=%r payload=%s attempt=%s", query_str, payload, attempt + 1)
                timeout = int(os.environ.get("GOOGLE_PLACES_TIMEOUT_SECONDS", "10"))
                res = requests.post(url, json=payload, headers=headers, timeout=timeout)
                if res.status_code == 200:
                    response = res.json()
                    logger.info("PROVIDER_RAW_RESPONSE provider=google_places query=%r result_count=%s data=%s", query_str, len(response.get("places", [])), response)
                    break
                elif res.status_code in [429, 500, 503]:
                    logger.warning(f"Transient error from Google Places ({res.status_code}). Retrying...")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    raise DiscoveryError(f"Google Places API returned HTTP {res.status_code}.")
            except requests.RequestException as req_err:
                if attempt == retries - 1:
                    raise DiscoveryError(f"Google Places API request failed: {req_err}")
                time.sleep(backoff)
                backoff *= 2

        if response is None:
            raise DiscoveryError("Google Places API failed to return response after retries.")

        raw_places = response.get("places", [])
        result_items: List[DiscoveryResultItem] = []

        for place in raw_places:
            place_id = place.get("id")
            display_name_dict = place.get("displayName", {})
            name = display_name_dict.get("text", "Unknown Business")
            address = place.get("formattedAddress")
            phone = place.get("nationalPhoneNumber")
            website = place.get("websiteUri")
            category = place.get("primaryType")
            rating = place.get("rating", 0.0)

            result_items.append(
                DiscoveryResultItem(
                    name=name,
                    website=website,
                    phone=phone,
                    address=address,
                    category=category,
                    rating=float(rating),
                    external_id=place_id,
                    raw_reference=place
                )
            )

        return DiscoveryResult(
            provider=self.name,
            request_id=response.get("nextPageToken", "gplaces-req"),
            results=result_items,
            next_page_token=response.get("nextPageToken"),
            usage={"places_count": len(result_items), "estimated_cost": self.estimate_cost(request)},
            raw_metadata=response
        )

# Auto-register to registry on module load
discovery_provider_registry.register(GooglePlacesProvider())
