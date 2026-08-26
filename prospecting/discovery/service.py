import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Set
from django.conf import settings
from django.db.models import Q
from knowledge_base.models import UserProfile
from prospecting.models import (
    ProspectingCampaign, DiscoveryRun, LeadCompany, DiscoveryLead,
    CampaignLeadInsight, ProspectingSpecificationVersion
)
from prospecting.discovery.dto import DiscoveryResultItem
from prospecting.discovery.deduplication import Deduplicator
from prospecting.discovery.tracing import DiscoveryTraceRecorder

logger = logging.getLogger(__name__)


class DiscoveryBatchService:
    """
    Service responsible for batch lead discovery continuation ("Generate More Leads").
    Discovers additional target leads for an existing ProspectingCampaign using its
    confirmed ProspectingSpecification while strictly deduplicating against all existing
    leads already in the campaign.
    """

    @classmethod
    def generate_batch(
        cls,
        campaign: ProspectingCampaign,
        batch_size: int = 10,
        discovery_run: Optional[DiscoveryRun] = None,
        user_profile: Optional[UserProfile] = None
    ) -> Dict[str, Any]:
        """
        Execute an incremental lead discovery batch for the given campaign.
        Strictly excludes any leads already present in this campaign.
        """
        user = user_profile or campaign.created_by
        run = discovery_run
        
        # 1. Resolve Prospecting Specification and search parameters
        spec_version = None
        spec = None
        if campaign.prospecting_request:
            spec_version = campaign.prospecting_request.spec_versions.filter(
                status='CONFIRMED'
            ).last() or campaign.prospecting_request.spec_versions.last()

        location = "UK"
        search_categories = []

        if spec_version and spec_version.specification_json:
            try:
                from prospecting.intent.schemas import ProspectingSpecification
                spec = ProspectingSpecification.model_validate(spec_version.specification_json)
                if spec.geography and spec.geography.cities.value:
                    location = ", ".join(spec.geography.cities.value)
                elif spec.geography and spec.geography.countries.value:
                    location = ", ".join(spec.geography.countries.value)
                elif spec.geography and spec.geography.scope.value:
                    location = spec.geography.scope.value

                if spec.target and spec.target.categories.value:
                    search_categories = [c.strip() for c in spec.target.categories.value if c.strip()]
                elif spec.target and spec.target.industries.value:
                    search_categories = [i.strip() for i in spec.target.industries.value if i.strip()]
            except Exception as e:
                logger.warning(f"Could not parse specification for campaign {campaign.id}: {e}")

        if not location:
            location = campaign.geography.get("location") or campaign.geography.get("country") or "UK"

        if not search_categories:
            if campaign.description:
                search_categories = [campaign.description]
            elif campaign.name:
                search_categories = [campaign.name]
            else:
                search_categories = ["logistics"]

        # If run wasn't supplied, create one
        if not run:
            keyword = search_categories[0] if search_categories else "Business"
            run = DiscoveryRun.objects.create(
                user_profile=user,
                campaign=campaign,
                prospecting_request=campaign.prospecting_request,
                specification_version=spec_version,
                keyword=keyword,
                location=location,
                status='running',
                total_leads_found=0
            )

        trace = DiscoveryTraceRecorder(str(run.id))
        trace.initialize({
            "keyword": run.keyword,
            "location": run.location,
            "campaign_id": str(campaign.id),
            "batch_type": "generate_more",
            "requested_batch_size": batch_size,
        })

        # 2. Collect past queried terms for query rotation
        previous_runs = DiscoveryRun.objects.filter(campaign=campaign).exclude(id=run.id)
        queried_terms_history: Set[str] = set()
        for p_run in previous_runs:
            if p_run.keyword:
                queried_terms_history.add(p_run.keyword.lower().strip())

        # Determine queries for this batch
        unqueried_categories = [c for c in search_categories if c.lower().strip() not in queried_terms_history]
        queries_to_run = unqueried_categories if unqueried_categories else search_categories

        # If all categories have been queried and none remain, call search planner for expansions
        if not unqueried_categories and spec:
            try:
                from llm.router import IntelligentRouter
                import json
                router = IntelligentRouter()
                planner_prompt = (
                    "Generate 3 new and distinct business search category queries for lead discovery. "
                    "Each term must be 1 to 4 words naming a public business category without locations or buzzwords.\n"
                    f"Target description: {spec.target.description.value}\n"
                    f"Previously queried terms: {list(queried_terms_history)}\n"
                    'Return ONLY JSON: {"search_queries":["new category one","new category two"]}'
                )
                res = router.generate(
                    prompt=planner_prompt,
                    system_prompt="You are a helpful search optimization planner. Respond in raw JSON.",
                    prompt_key="prospecting.search_planner.user",
                    system_prompt_key="prospecting.search_planner.system",
                    template_variables={
                        "target_description": spec.target.description.value,
                        "objective": spec.objective.value
                    }
                )
                if res.get("type") == "text":
                    parsed = json.loads(res.get("text", "{}"))
                    additional_terms = parsed.get("search_queries", [])
                    if additional_terms:
                        queries_to_run = additional_terms
            except Exception as plan_err:
                logger.warning(f"Search planner continuation fallback: {plan_err}")

        # 3. Setup tools and healthy providers
        from llm.tools.context import ToolContext
        from llm.tools.executor import ToolExecutor
        from prospecting.orchestrator import ProspectingToolOrchestrator
        from llm.providers.registry import provider_registry

        orchestrator = ProspectingToolOrchestrator()
        executor = ToolExecutor(orchestrator.tool_registry)
        context = ToolContext(run_id=str(run.id), source="workflow")

        company_provider_names = []
        for name in ["google_places", "apollo", "apify", "openstreetmap"]:
            try:
                prov = provider_registry.get(name)
                is_healthy = prov.health_check() if hasattr(prov, "health_check") else True
                if is_healthy:
                    company_provider_names.append(name)
            except Exception as prov_err:
                logger.warning(f"Provider {name} unavailable: {prov_err}")

        web_provider_names = []
        for name in ["duckduckgo"]:
            try:
                prov = provider_registry.get(name)
                is_healthy = prov.health_check() if hasattr(prov, "health_check") else True
                if is_healthy:
                    web_provider_names.append(name)
            except Exception as prov_err:
                logger.warning(f"Web provider {name} unavailable: {prov_err}")

        # 4. Fetch existing campaign leads to enforce CAMPAIGN-SCOPED DEDUPLICATION
        existing_campaign_company_ids = set(
            LeadCompany.objects.filter(
                Q(campaign=campaign) |
                Q(discovery_run__campaign=campaign) |
                Q(discovery_leads__discovery_run__campaign=campaign)
            ).values_list('id', flat=True)
        )

        discovered_candidates: List[DiscoveryResultItem] = []
        seen_candidate_keys = set()

        def useful_category(provider_cat, fallback_cat):
            raw = str(provider_cat or "").strip()
            generic = {"", "web search", "service", "point_of_interest", "establishment", "business", "company"}
            if raw.lower() not in generic:
                cleaned = raw.replace("_", " ")
            else:
                cleaned = fallback_cat.strip().replace("_", " ")
            return (cleaned if cleaned.isupper() else cleaned.title())[:100] or None

        # Execute searches across queries and providers
        for query_term in queries_to_run:
            # Query company search providers
            for provider_name in company_provider_names:
                try:
                    tool_result = executor.execute(
                        "search_companies",
                        {
                            "query": query_term,
                            "geography": location,
                            "limit": 20,
                            "provider": provider_name
                        },
                        context=context
                    )
                    if tool_result.success:
                        companies = (tool_result.data or {}).get("companies", [])
                        for c in companies:
                            name = (c.get("name") or "").strip()
                            if not name:
                                continue
                            website = (c.get("website") or "").strip()
                            key = (name.lower(), website.lower())
                            if key in seen_candidate_keys:
                                continue
                            seen_candidate_keys.add(key)
                            discovered_candidates.append(
                                DiscoveryResultItem(
                                    name=name,
                                    website=website or None,
                                    phone=c.get("phone"),
                                    address=c.get("address"),
                                    category=useful_category(c.get("category"), query_term),
                                    external_id=c.get("external_id"),
                                    raw_reference={"source_provider": provider_name, "query": query_term}
                                )
                            )
                except Exception as comp_err:
                    logger.warning(f"Provider {provider_name} search failed for '{query_term}': {comp_err}")

            # Query web search providers
            for provider_name in web_provider_names:
                try:
                    from prospecting.tasks import build_duckduckgo_queries
                    for web_query in build_duckduckgo_queries(query_term, location)[:3]:
                        tool_result = executor.execute(
                            "search_web",
                            {"query": web_query, "limit": 20},
                            context=context
                        )
                        if tool_result.success:
                            results = (tool_result.data or {}).get("results", [])
                            for item in results:
                                name = (item.get("name") or item.get("title") or "").strip()
                                website = (item.get("url") or "").strip()
                                if not name or not website:
                                    continue
                                key = (name.lower(), website.lower())
                                if key in seen_candidate_keys:
                                    continue
                                seen_candidate_keys.add(key)
                                discovered_candidates.append(
                                    DiscoveryResultItem(
                                        name=name,
                                        website=website,
                                        address=location or None,
                                        category=useful_category(None, query_term),
                                        external_id=website,
                                        raw_reference={"source_provider": provider_name, "query": web_query}
                                    )
                                )
                except Exception as web_err:
                    logger.warning(f"Web search failed for '{query_term}': {web_err}")

        # 5. Entity Deduplication and Campaign-Level Filtering
        newly_added_leads: List[LeadCompany] = []
        duplicate_skipped_count = 0

        for item in discovered_candidates:
            if len(newly_added_leads) >= batch_size:
                break

            existing_company = Deduplicator.find_existing_company(item)
            if existing_company:
                # Check if this company is already in this campaign
                if existing_company.id in existing_campaign_company_ids:
                    duplicate_skipped_count += 1
                    continue
                else:
                    # Associate existing company to this campaign
                    DiscoveryLead.objects.get_or_create(discovery_run=run, company=existing_company)
                    CampaignLeadInsight.objects.get_or_create(company=existing_company, campaign=campaign)
                    existing_campaign_company_ids.add(existing_company.id)
                    newly_added_leads.append(existing_company)
            else:
                # Create brand new LeadCompany
                new_company = LeadCompany.objects.create(
                    discovery_run=run,
                    campaign=campaign,
                    name=item.name[:255] if item.name else "Unknown",
                    website=item.website[:2000] if item.website else None,
                    phone=item.phone[:100] if item.phone else None,
                    address=item.address,
                    category=item.category[:100] if item.category else None,
                    enrichment_status='NOT_STARTED'
                )
                DiscoveryLead.objects.get_or_create(discovery_run=run, company=new_company)
                CampaignLeadInsight.objects.get_or_create(company=new_company, campaign=campaign)
                existing_campaign_company_ids.add(new_company.id)
                newly_added_leads.append(new_company)

        # 6. Check for search space exhaustion
        is_exhausted = (len(newly_added_leads) < batch_size)

        # 7. Finalize DiscoveryRun
        run.status = 'completed'
        run.total_leads_found = len(newly_added_leads)
        run.completed_at = datetime.now(timezone.utc)
        run.save(update_fields=['status', 'total_leads_found', 'completed_at'])

        trace.event(
            "completion",
            "Batch discovery completed",
            actor="workflow",
            input_data={"queries": queries_to_run, "requested_limit": batch_size},
            output_data={
                "leads_created": len(newly_added_leads),
                "duplicates_skipped": duplicate_skipped_count,
                "exhausted": is_exhausted
            }
        )

        logger.info(
            f"Generated {len(newly_added_leads)} leads for campaign '{campaign.name}' "
            f"(requested {batch_size}, exhausted={is_exhausted})."
        )

        return {
            "status": "success",
            "run_id": str(run.id),
            "campaign_id": str(campaign.id),
            "requested_limit": batch_size,
            "leads_created": len(newly_added_leads),
            "exhausted": is_exhausted,
            "lead_ids": [str(c.id) for c in newly_added_leads],
        }
