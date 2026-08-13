import os
import time
import logging
import requests
from typing import List
from prospecting.exceptions import DiscoveryError
from prospecting.discovery.dto import DiscoveryRequest, DiscoveryResult, DiscoveryResultItem
from prospecting.discovery.providers.base import BusinessDiscoveryProvider
from prospecting.discovery.providers.registry import discovery_provider_registry

logger = logging.getLogger(__name__)

class ApifyProvider(BusinessDiscoveryProvider):
    @property
    def name(self) -> str:
        return "apify"

    def health_check(self) -> bool:
        return bool(os.environ.get("APIFY_API_TOKEN", "").strip())

    def capabilities(self) -> list:
        return ["deep_scraping", "dataset_polling"]

    def estimate_cost(self, request: DiscoveryRequest) -> float:
        # Apify cost estimate is roughly $0.05 per run in API credits
        return 0.05

    def search(self, request: DiscoveryRequest) -> DiscoveryResult:
        request.validate()
        token = os.environ.get("APIFY_API_TOKEN", "").strip()
        actor_id = os.environ.get("APIFY_GOOGLE_MAPS_ACTOR_ID", "apify/google-maps-scraper").strip()
        timeout = int(os.environ.get("APIFY_TIMEOUT_SECONDS", "300"))
        poll_interval = int(os.environ.get("APIFY_POLL_INTERVAL_SECONDS", "10"))

        if not token:
            raise DiscoveryError("Apify API Token is not configured.")

        # 1. Start the Actor Run
        start_url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={token}"
        query_str = f"{request.query} in {request.location}"
        
        payload = {
            "searchStrings": [query_str],
            "maxCrawledPlacesPerSearch": min(request.limit, 20),
            "exportPlaceUrls": False
        }

        try:
            logger.info(f"Triggering Apify Google Maps Scraper Actor run for: '{query_str}'")
            res = requests.post(start_url, json=payload, timeout=15)
            if res.status_code not in [200, 201]:
                raise DiscoveryError(f"Failed to start Apify actor. Status: {res.status_code}. Response: {res.text}")
            run_data = res.json().get("data", {})
            run_id = run_data.get("id")
            dataset_id = run_data.get("defaultDatasetId")
            if not run_id or not dataset_id:
                raise DiscoveryError("Apify run triggered but failed to return Run ID or Dataset ID.")
        except requests.RequestException as e:
            raise DiscoveryError(f"Failed to communicate with Apify during initiation: {e}")

        # 2. Poll the Actor Run
        poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
        start_time = time.time()
        status = "RUNNING"

        while status in ["RUNNING", "READY"]:
            if time.time() - start_time > timeout:
                logger.warning(f"Apify Actor run {run_id} timed out after {timeout} seconds. Attempting to fetch partial results.")
                break
            
            try:
                logger.info(f"Polling Apify Actor run status for: {run_id}")
                res = requests.get(poll_url, timeout=10)
                if res.status_code == 200:
                    status = res.json().get("data", {}).get("status", "RUNNING")
                    if status in ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"]:
                        break
                time.sleep(poll_interval)
            except requests.RequestException as poll_err:
                logger.warning(f"Apify polling error: {poll_err}")
                time.sleep(poll_interval)

        if status == "FAILED":
            raise DiscoveryError(f"Apify actor run failed for ID: {run_id}")
        elif status == "ABORTED":
            raise DiscoveryError(f"Apify actor run was aborted for ID: {run_id}")

        # 3. Retrieve Scraped Items Dataset
        dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}"
        try:
            logger.info(f"Retrieving Apify dataset items for Dataset ID: {dataset_id}")
            res = requests.get(dataset_url, timeout=20)
            if res.status_code != 200:
                raise DiscoveryError(f"Failed to fetch dataset items. Status: {res.status_code}")
            items = res.json()
        except requests.RequestException as e:
            raise DiscoveryError(f"Failed to fetch dataset from Apify: {e}")

        # 4. Normalize results
        result_items: List[DiscoveryResultItem] = []
        for item in items:
            name = item.get("title", "Unknown Business")
            website = item.get("website")
            phone = item.get("phone")
            address = item.get("address")
            category = item.get("categoryName")
            rating = item.get("totalScore", 0.0)
            place_id = item.get("placeId")

            result_items.append(
                DiscoveryResultItem(
                    name=name,
                    website=website,
                    phone=phone,
                    address=address,
                    category=category,
                    rating=float(rating),
                    external_id=place_id,
                    raw_reference=item
                )
            )

        return DiscoveryResult(
            provider=self.name,
            request_id=run_id,
            results=result_items,
            next_page_token=None,
            usage={"run_id": run_id, "dataset_id": dataset_id, "scraped_count": len(result_items), "estimated_cost": self.estimate_cost(request)},
            raw_metadata={"run_status": status, "items_count": len(items)}
        )

# Auto-register to registry on module load
discovery_provider_registry.register(ApifyProvider())
