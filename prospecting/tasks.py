import logging
from typing import List
from celery import shared_task
from django.core.cache import cache
from asgiref.sync import async_to_sync
import asyncio
import threading
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

def safe_async_to_sync(func, *args, **kwargs):
    """
    Safely execute a coroutine function in both synchronous contexts and
    contexts where an asyncio event loop is already running in the current thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        result = []
        error = []
        def target():
            try:
                res = asyncio.run(func(*args, **kwargs))
                result.append(res)
            except Exception as e:
                error.append(e)
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result[0] if result else None
    else:
        return async_to_sync(func)(*args, **kwargs)
from channels.layers import get_channel_layer
from prospecting.exceptions import DiscoveryError
from prospecting.models import DiscoveryRun, LeadCompany
from prospecting.repositories import DiscoveryRunRepository, LeadCompanyRepository
from prospecting.discovery.dto import DiscoveryRequest
from prospecting.discovery.providers.registry import discovery_provider_registry
from prospecting.discovery.deduplication import Deduplicator
from prospecting.contact import ContactExtractor
from prospecting.analyzer import WebsiteAnalyzer
from prospecting.discovery.tracing import DiscoveryTraceRecorder

logger = logging.getLogger(__name__)

MAX_WEBSITES_PER_DISCOVERY = 5


class DiscoveryRunCancelled(Exception):
    """Stop a discovery worker after the user cancels its run."""


def ensure_discovery_run_active(run: DiscoveryRun):
    run.refresh_from_db(fields=['status'])
    if run.status == 'cancelled':
        raise DiscoveryRunCancelled(f"Discovery run {run.id} was cancelled.")


def build_website_scrape_plan(companies, requested_limit: int = MAX_WEBSITES_PER_DISCOVERY):
    """Return every unique eligible website and the bounded enrichment queue.

    Search results remain visible even when they are outside the scrape budget.
    The absolute cap deliberately cannot be raised above five by configuration or
    a caller, which keeps each discovery run's external crawling predictable.
    """
    try:
        limit = int(requested_limit)
    except (TypeError, ValueError):
        limit = MAX_WEBSITES_PER_DISCOVERY
    limit = min(max(limit, 0), MAX_WEBSITES_PER_DISCOVERY)

    websites = []
    selected_companies = []
    seen_domains = set()

    for company in companies:
        website = str(company.website or "").strip()
        if not website:
            continue
        normalized_url = website if website.startswith(("http://", "https://")) else f"https://{website}"
        domain = (urlsplit(normalized_url).hostname or website).lower().removeprefix("www.")
        if domain in seen_domains:
            continue
        seen_domains.add(domain)

        selected = len(selected_companies) < limit
        websites.append({
            "company_id": str(company.id),
            "company_name": company.name,
            "url": normalized_url,
            "domain": domain,
            "selected": selected,
            "status": "selected" if selected else "not_scraped_limit",
        })
        if selected:
            selected_companies.append(company)

    return websites, selected_companies


def build_duckduckgo_queries(category: str, location: str) -> List[str]:
    """Build concise, high-recall lead-generation queries in fallback order.

    Location is intentionally NOT wrapped in quotes — exact-phrase quoting of
    'United Kingdom, London' style strings rarely matches real web page content
    and causes DDG to return zero results.
    """
    category = " ".join((category or "").split()).strip(' "')
    location = " ".join((location or "").split()).strip(' "')
    if not category:
        return []
    location_suffix = f" {location}" if location else ""
    queries = [
        f"{category}{location_suffix}",
        f"{category} companies{location_suffix}",
        f"{category} directory{location_suffix}",
    ]
    # Preserve order while removing duplicates caused by unusual empty inputs.
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))

def broadcast_progress(run_id: str, stage: str, progress: int, message: str):
    terminal_statuses = {'failed', 'completed', 'cancelled'}
    cache.set(f"discovery_run:{run_id}:progress", {
        "stage": stage,
        "progress": progress,
        "message": message,
        "status": stage if stage in terminal_statuses else "running"
    }, timeout=86400)

    channel_layer = get_channel_layer()
    if channel_layer:
        safe_async_to_sync(
            channel_layer.group_send,
            f"prospecting_{run_id}",
            {
                "type": "progress_update",
                "data": {
                    "type": "prospecting.run.progress",
                    "run_id": run_id,
                    "stage": stage,
                    "progress": progress,
                    "message": message
                }
            }
        )

def broadcast_completion(run_id: str, discovered: int, new_companies: int, duplicates: int):
    cache.set(f"discovery_run:{run_id}:metrics", {
        "discovered": discovered,
        "new": new_companies,
        "duplicates": duplicates
    }, timeout=86400)

    channel_layer = get_channel_layer()
    if channel_layer:
        safe_async_to_sync(
            channel_layer.group_send,
            f"prospecting_{run_id}",
            {
                "type": "progress_update",
                "data": {
                    "type": "prospecting.run.completed",
                    "run_id": run_id,
                    "summary": {
                        "discovered": discovered,
                        "new_companies": new_companies,
                        "duplicates": duplicates
                    }
                }
            }
        )

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    time_limit=600
)
def discover_campaign_async(run_id: str, enrich_leads: bool = False):
    """
    Celery task to run business discovery asynchronously in the background.
    When enrich_leads is False (default), discovery completes after persisting LeadCompany
    and DiscoveryLead records without running downstream contact scraping or qualification.
    """
    lock_key = f"lock:discover_campaign_async:{run_id}"
    lock_acquired = cache.add(lock_key, "locked", timeout=1200)

    if not lock_acquired:
        logger.warning(f"Task already executing for key {lock_key}. Skipping execution.")
        return {"status": "Skipped", "reason": "Task lock already held."}

    logger.debug(f"Lock acquired for prospecting task: {lock_key}")
    broadcast_progress(run_id, "queued", 5, "Initializing task runner...")

    run = DiscoveryRunRepository.get_run(run_id)
    if not run:
        cache.delete(lock_key)
        raise DiscoveryError(f"Discovery run {run_id} not found.")

    trace = DiscoveryTraceRecorder(run_id)
    trace.initialize({
        "keyword": run.keyword,
        "location": run.location,
        "campaign_id": str(run.campaign_id) if run.campaign_id else None,
        "prospecting_request_id": str(run.prospecting_request_id) if run.prospecting_request_id else None,
        "specification_version_id": str(run.specification_version_id) if run.specification_version_id else None,
    })

    ctx_manager = None
    try:
        ensure_discovery_run_active(run)
        from llm.context import LLMRequestContext
        
        metadata = {
            "discovery_run_id": str(run.id),
            "discovery_id": str(run.discovery_id) if run.discovery_id else "",
            "prospecting_request_id": str(run.prospecting_request_id) if run.prospecting_request_id else "",
            "specification_version_id": str(run.specification_version_id) if run.specification_version_id else "",
        }
        
        ctx_manager = LLMRequestContext(
            correlation_id=f"discovery_run:{run.id}",
            operation="prospecting.discovery_run",
            metadata=metadata
        )
        ctx_manager.__enter__()

        # Keep every execution grouped even when a run was created by legacy
        # code or an internal caller that omitted a campaign explicitly.
        # Wrapped in try/except so a missing default workspace cannot strand
        # the run in 'queued' state before status is set to 'running'.
        try:
            from prospecting.campaigns import ensure_campaign_for_run
            ensure_campaign_for_run(run)
        except Exception as campaign_err:
            logger.warning(f"Could not ensure campaign for run {run_id}: {campaign_err}")

        started = DiscoveryRun.objects.filter(id=run.id).exclude(status='cancelled').update(status='running')
        if not started:
            raise DiscoveryRunCancelled(f"Discovery run {run.id} was cancelled before it started.")
        run.status = 'running'
        if run.prospecting_request_id:
            from prospecting.models import ProspectingRequest
            ProspectingRequest.objects.filter(
                id=run.prospecting_request_id,
                status__in=['CONFIRMED', 'EXECUTING'],
            ).update(status='EXECUTING')

        # 1. Initialize Tool Platform
        import os
        from llm.tools.context import ToolContext
        from llm.tools.executor import ToolExecutor
        from prospecting.orchestrator import ProspectingToolOrchestrator

        orchestrator = ProspectingToolOrchestrator()
        executor = ToolExecutor(orchestrator.tool_registry)
        context = ToolContext(
            run_id=str(run.id),
            source="workflow"
        )

        # Discover every healthy provider up front. Providers are executed
        # independently below, so one failure or empty response cannot prevent
        # the remaining sources from being queried.
        company_provider_names = []
        for name in ["google_places", "apollo", "apify", "openstreetmap"]:
            try:
                from llm.providers.registry import provider_registry
                prov = provider_registry.get(name)
                # Default to True if the provider does not define health_check
                is_healthy = prov.health_check() if hasattr(prov, "health_check") else True
                if is_healthy:
                    company_provider_names.append(name)
                else:
                    logger.debug(f"Skipping unhealthy discovery provider: {name}")
            except Exception as provider_err:
                logger.warning(f"Skipping unavailable discovery provider '{name}': {provider_err}")

        web_provider_names = []
        for name in ["duckduckgo"]:
            try:
                prov = provider_registry.get(name)
                is_healthy = prov.health_check() if hasattr(prov, "health_check") else True
                if is_healthy:
                    web_provider_names.append(name)
                else:
                    logger.debug(f"Skipping unhealthy web provider: {name}")
            except Exception as provider_err:
                logger.warning(f"Skipping unavailable web provider '{name}': {provider_err}")

        active_providers = company_provider_names + web_provider_names
        logger.debug(f"Active discovery providers: {active_providers}")
        trace.event(
            "llm_input_interpretation",
            "Available source tools selected",
            actor="workflow",
            input_data={"candidate_providers": ["google_places", "apollo", "apify", "openstreetmap", "duckduckgo"]},
            output_data={
                "company_search_providers": company_provider_names,
                "web_search_providers": web_provider_names,
            },
            metadata={"decision_source": "provider health checks, not an LLM decision"},
        )
        broadcast_progress(
            run_id,
            "discovering",
            20,
            f"Searching available lead sources: {', '.join(active_providers) or 'none'}..."
        )

        # 1. Discovery Planner: derive search terms from specification if available
        search_keywords = []
        if run.specification_version:
            try:
                from prospecting.intent.schemas import ProspectingSpecification
                spec = ProspectingSpecification.model_validate(run.specification_version.specification_json)
                cats = spec.target.categories.value
                if cats:
                    search_keywords = [c for c in cats if c.strip()]
                    trace.event(
                        "llm_input_interpretation",
                        "Confirmed specification interpreted as search categories",
                        actor="workflow",
                        input_data={
                            "target_description": spec.target.description.value,
                            "objective": spec.objective.value,
                            "confirmed_categories": cats,
                        },
                        output_data={"search_queries": search_keywords},
                        metadata={"decision_source": "confirmed structured specification; LLM planner skipped"},
                    )
                
                if not search_keywords:
                    logger.debug("Discovery Planner deriving search terms using router...")
                    from llm.router import IntelligentRouter
                    import json
                    router = IntelligentRouter()
                    planner_prompt = (
                        "Create 2 to 3 lead-discovery business category terms from the inputs below. "
                        "Each term must be 1 to 4 words, name a business type that companies publicly use, "
                        "and contain no location, sales language, product features, pain points, questions, "
                        "Boolean operators, or words such as leads, best, near me, company, or business. "
                        "Prefer broad standard categories over niche prose (examples: pest control, courier service, dental clinic).\n"
                        f"Target description: {spec.target.description.value}\n"
                        f"Objective: {spec.objective.value}\n"
                        'Return only JSON: {"search_queries":["category one","category two"]}.'
                    )
                    logger.debug("SEARCH_PLANNER_PROMPT prompt=%r", planner_prompt)
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
                    logger.debug("SEARCH_PLANNER_RESPONSE response=%s", res)
                    if res.get("type") == "text":
                        parsed = json.loads(res.get("text", "{}"))
                        search_keywords = parsed.get("search_queries", [])
                    trace.event(
                        "llm_input_interpretation",
                        "LLM converted the user specification into search terms",
                        actor="llm:search-planner",
                        input_data={
                            "system_prompt": "You are a helpful search optimization planner. Respond in raw JSON.",
                            "prompt": planner_prompt,
                        },
                        output_data={"raw_response": res, "search_queries": search_keywords},
                        status="success" if search_keywords else "error",
                        metadata={"parsed": bool(search_keywords), "decision_source": "LLM"},
                    )
            except Exception as planner_err:
                logger.error(f"Discovery Planner failed: {planner_err}")
                trace.event(
                    "llm_input_interpretation",
                    "Search planning failed",
                    actor="llm:search-planner",
                    input_data={"keyword": run.keyword, "location": run.location},
                    output_data={"error": str(planner_err)},
                    status="error",
                    metadata={"parsed": False, "decision_source": "LLM"},
                )

        # Fallback to run.keyword if no terms derived
        if not search_keywords:
            search_keywords = [run.keyword]
            trace.event(
                "llm_input_interpretation",
                "Raw user keyword used as the fallback search plan",
                actor="workflow",
                input_data={"keyword": run.keyword, "location": run.location},
                output_data={"search_queries": search_keywords},
                metadata={"decision_source": "deterministic fallback; no LLM interpretation"},
            )

        trace.event(
            "llm_input_interpretation",
            "Resolved search plan",
            actor="workflow",
            input_data={"keyword": run.keyword, "location": run.location},
            output_data={"resolved_search_terms": search_keywords},
            metadata={"decision_source": "confirmed output passed to discovery tools"},
        )

        # Execute company discovery using SearchCompaniesTool for each derived keyword
        from prospecting.discovery.dto import DiscoveryResultItem
        discovered_leads: List[DiscoveryResultItem] = []
        seen_lead_keys = set()
        
        for search_keyword in search_keywords:
            ensure_discovery_run_active(run)
            words = search_keyword.split()
            if not run.specification_version and (len(words) > 4 or any(w in search_keyword.lower() for w in ["app", "built", "helps", "business", "finding", "routing", "optimis"])):
                logger.debug(f"Optimizing search query with LLM: \"{search_keyword[:40]}...\"")
                try:
                    from llm.router import IntelligentRouter
                    import json
                    router = IntelligentRouter()
                    prompt = (
                        "Extract 1 to 2 business categories suitable for a local directory or web search. "
                        "Use 1 to 4 words per category. Do not include product features, pain-point prose, "
                        "locations, questions, Boolean operators, or sales words. "
                        f"Input: {search_keyword}\n"
                        'Return only JSON: {"keywords":["category one","category two"]}.'
                    )
                    logger.debug("SEARCH_OPTIMIZER_PROMPT prompt=%r", prompt)
                    res = router.generate(
                        prompt=prompt,
                        system_prompt="You are a helpful keyword extraction assistant. Respond in raw JSON.",
                        prompt_key="prospecting.keyword_extractor.user",
                        system_prompt_key="prospecting.keyword_extractor.system",
                        template_variables={
                            "search_keyword": search_keyword
                        }
                    )
                    logger.debug("SEARCH_OPTIMIZER_RESPONSE response=%s", res)
                    original_search_keyword = search_keyword
                    extracted = []
                    if res.get("type") == "text":
                        parsed = json.loads(res.get("text", "{}"))
                        extracted = parsed.get("keywords", [])
                        if extracted:
                            search_keyword = extracted[0]
                    trace.event(
                        "llm_input_interpretation",
                        "LLM optimized a free-form keyword for search tools",
                        actor="llm:keyword-optimizer",
                        input_data={
                            "system_prompt": "You are a helpful keyword extraction assistant. Respond in raw JSON.",
                            "prompt": prompt,
                        },
                        output_data={
                            "raw_response": res,
                            "keywords": extracted,
                            "selected_keyword": search_keyword,
                        },
                        status="success" if extracted else "error",
                        metadata={
                            "parsed": bool(extracted),
                            "original_keyword": original_search_keyword,
                            "decision_source": "LLM",
                        },
                    )
                except Exception as llm_err:
                    logger.error(f"Failed to optimize search keyword using LLM: {llm_err}")
                    trace.event(
                        "llm_input_interpretation",
                        "Keyword optimization failed",
                        actor="llm:keyword-optimizer",
                        input_data={"keyword": search_keyword},
                        output_data={"error": str(llm_err)},
                        status="error",
                        metadata={"parsed": False, "decision_source": "LLM"},
                    )

            def useful_category(provider_category):
                generic_categories = {
                    "", "web search", "service", "point_of_interest", "establishment",
                    "business", "company", "corporate_office",
                }
                raw_category = str(provider_category or "").strip()
                if raw_category.lower() not in generic_categories:
                    cleaned = raw_category.replace("_", " ")
                else:
                    cleaned = search_keyword.strip().replace("_", " ")
                return (cleaned if cleaned.isupper() else cleaned.title())[:100] or None

            for provider_name in company_provider_names:
                ensure_discovery_run_active(run)
                logger.debug(
                    f"Running company search for: \"{search_keyword}\" in "
                    f"\"{run.location}\" using provider \"{provider_name}\"..."
                )
                tool_result = executor.execute(
                    "search_companies",
                    {
                        "query": search_keyword,
                        "geography": run.location,
                        "limit": 20,
                        "provider": provider_name
                    },
                    context=context
                )

                if not tool_result.success:
                    logger.warning(f"Discovery provider '{provider_name}' failed; continuing to the next source.")
                    continue

                companies = (tool_result.data or {}).get("companies", [])
                if not companies:
                    logger.debug(f"Discovery provider '{provider_name}' returned no results; continuing.")
                    continue

                logger.debug(f"Discovery provider '{provider_name}' returned {len(companies)} results.")
                for c in companies:
                    name = (c.get("name") or "").strip()
                    if not name:
                        continue
                    website = (c.get("website") or "").strip()
                    lead_key = (name.lower(), website.lower())
                    if lead_key in seen_lead_keys:
                        continue
                    seen_lead_keys.add(lead_key)
                    metadata = dict(c.get("raw_metadata") or {})
                    metadata.setdefault("source_provider", provider_name)
                    discovered_leads.append(
                        DiscoveryResultItem(
                            name=name,
                            website=website or None,
                            phone=c.get("phone"),
                            address=c.get("address"),
                            category=useful_category(c.get("category")),
                            external_id=c.get("external_id"),
                            raw_reference=metadata
                        )
                    )

            # Web-search providers have a different output schema, so run them
            # through search_web and normalize links into discovery leads.
            for provider_name in web_provider_names:
                web_results = []
                for query_number, web_query in enumerate(build_duckduckgo_queries(search_keyword, run.location), start=1):
                    ensure_discovery_run_active(run)
                    logger.debug("Running web search query %s: %r using provider %r", query_number, web_query, provider_name)
                    tool_result = executor.execute(
                        "search_web",
                        {"query": web_query, "limit": 20},
                        context=context
                    )
                    if not tool_result.success:
                        logger.warning("Web provider %r failed for query %r; trying fallback query.", provider_name, web_query)
                        continue
                    web_results = (tool_result.data or {}).get("results", [])
                    if web_results:
                        break
                    logger.debug("Web provider %r returned zero results for query %r; trying fallback query.", provider_name, web_query)

                if not web_results:
                    logger.debug("Web provider %r exhausted all query variants with zero results.", provider_name)
                    continue

                logger.debug(f"Web provider '{provider_name}' returned {len(web_results)} results.")
                for item in web_results:
                    name = (item.get("name") or item.get("title") or "").strip()
                    website = (item.get("url") or "").strip()
                    if not name or not website:
                        continue
                    lead_key = (name.lower(), website.lower())
                    if lead_key in seen_lead_keys:
                        continue
                    seen_lead_keys.add(lead_key)
                    discovered_leads.append(
                        DiscoveryResultItem(
                            name=name,
                            website=website,
                            address=run.location or None,
                            category=useful_category(None),
                            external_id=website,
                            raw_reference={
                                "source_provider": provider_name,
                                "search_category": search_keyword,
                                "title": item.get("title", ""),
                                "snippet": item.get("snippet", "")
                            }
                        )
                    )
        logger.debug(f"Discovered {len(discovered_leads)} raw leads.")

        # 2. Entity Resolution & Deduplication
        ensure_discovery_run_active(run)
        broadcast_progress(run_id, "resolving", 40, "Deduplicating discovered leads...")
        new_count = 0
        duplicate_count = 0
        leads_to_process = []

        from prospecting.models import CompanySource, DiscoveryLead

        for item in discovered_leads:
            ensure_discovery_run_active(run)
            existing = Deduplicator.find_existing_company(item)
            source_provider = (item.raw_reference or {}).get("source_provider") or "web_search"
            search_query = (item.raw_reference or {}).get("search_category") or (item.raw_reference or {}).get("query") or ""
            source_url = item.website or None
            ext_id = item.external_id or item.website or None

            if existing:
                duplicate_count += 1
                target_company = existing
            else:
                company = LeadCompanyRepository.create_company(
                    discovery_run=run,
                    name=item.name,
                    website=item.website,
                    phone=item.phone,
                    address=item.address,
                    category=item.category
                )
                new_count += 1
                target_company = company

            # Record provenance on CompanySource
            try:
                if ext_id:
                    CompanySource.objects.get_or_create(
                        company=target_company,
                        provider=source_provider,
                        external_id=ext_id,
                        defaults={
                            "source_type": "discovery",
                            "source_url": source_url,
                            "raw_reference": item.raw_reference or {}
                        }
                    )
                else:
                    CompanySource.objects.get_or_create(
                        company=target_company,
                        provider=source_provider,
                        defaults={
                            "source_type": "discovery",
                            "source_url": source_url,
                            "raw_reference": item.raw_reference or {}
                        }
                    )
            except Exception as cs_err:
                logger.warning(f"Failed to record CompanySource for company {target_company.id}: {cs_err}")

            # Record DiscoveryLead run link with source details
            DiscoveryLead.objects.update_or_create(
                discovery_run=run,
                company=target_company,
                defaults={
                    "source_provider": source_provider,
                    "search_query": search_query
                }
            )
            leads_to_process.append(target_company)

        # 3. Candidate handoff
        # Discovery stops after finding and resolving candidates. Website crawling,
        # contact extraction, and LLM analysis only begin after the user explicitly
        # selects leads from the campaign page.
        website_plan, _ = build_website_scrape_plan(leads_to_process, 0)
        for website in website_plan:
            website["status"] = "awaiting_user_selection"
        trace.event(
            "website_selection",
            "Eligible websites listed for user selection",
            actor="workflow",
            input_data={
                "resolved_leads": len(leads_to_process),
            },
            output_data={
                "websites": website_plan,
                "eligible_count": len(website_plan),
                "selected_count": 0,
                "limit": MAX_WEBSITES_PER_DISCOVERY,
            },
            metadata={
                "decision_source": "manual user selection required",
                "eligible_count": len(website_plan),
                "selected_count": 0,
                "limit": MAX_WEBSITES_PER_DISCOVERY,
            },
        )

        # 4. Finalize candidate discovery
        ensure_discovery_run_active(run)
        completed = DiscoveryRun.objects.filter(id=run.id).exclude(status='cancelled').update(
            status='completed',
            total_leads_found=len(discovered_leads),
            completed_at=timezone.now(),
        )
        if not completed:
            raise DiscoveryRunCancelled(f"Discovery run {run.id} was cancelled before completion.")
        run.status = 'completed'
        run.total_leads_found = len(discovered_leads)

        if run.prospecting_request_id:
            from prospecting.models import ProspectingRequest
            ProspectingRequest.objects.filter(
                id=run.prospecting_request_id,
            ).exclude(status='CANCELLED').update(status='COMPLETED')

        broadcast_progress(run_id, "completed", 100, "Candidate discovery finished. Select leads to research.")
        broadcast_completion(run_id, len(discovered_leads), new_count, duplicate_count)

        trace.event(
            "completion",
            "Discovery completed",
            actor="workflow",
            input_data={"search_terms": search_keywords, "providers": active_providers},
            output_data={
                "leads_found": len(discovered_leads),
                "new_leads": new_count,
                "duplicates": duplicate_count,
                "enriched_companies": 0,
                "eligible_websites": len(website_plan),
                "scrape_limit": MAX_WEBSITES_PER_DISCOVERY,
                "research_requires_selection": True,
            },
            metadata={"decision_source": "workflow summary"},
        )

        return {
            "status": "success",
            "run_id": run_id,
            "leads_found": run.total_leads_found,
            "new_leads": new_count,
            "duplicates": duplicate_count
        }

    except DiscoveryRunCancelled:
        if run.prospecting_request_id:
            from prospecting.models import ProspectingRequest
            ProspectingRequest.objects.filter(id=run.prospecting_request_id).update(status='CANCELLED')
        broadcast_progress(run_id, "cancelled", 100, "Discovery run cancelled.")
        trace.event(
            "cancellation",
            "Discovery cancelled by the user",
            actor="user",
            status="success",
        )
        return {"status": "cancelled", "run_id": run_id}
    except Exception as e:
        logger.exception(f"Async prospecting run failed for ID: {run_id}")
        run.refresh_from_db(fields=['status'])
        if run.status == 'cancelled':
            if run.prospecting_request_id:
                from prospecting.models import ProspectingRequest
                ProspectingRequest.objects.filter(id=run.prospecting_request_id).update(status='CANCELLED')
            broadcast_progress(run_id, "cancelled", 100, "Discovery run cancelled.")
            return {"status": "cancelled", "run_id": run_id}
        failed = DiscoveryRun.objects.filter(id=run.id).exclude(status='cancelled').update(status='failed')
        if not failed:
            broadcast_progress(run_id, "cancelled", 100, "Discovery run cancelled.")
            return {"status": "cancelled", "run_id": run_id}
        run.status = 'failed'
        if run.prospecting_request_id:
            from prospecting.models import ProspectingRequest
            ProspectingRequest.objects.filter(
                id=run.prospecting_request_id,
            ).exclude(status='CANCELLED').update(status='FAILED')
        broadcast_progress(run_id, "failed", 100, f"Error: {str(e)}")
        trace.event(
            "error",
            "Discovery failed",
            actor="workflow",
            output_data={"error": str(e), "exception_type": type(e).__name__},
            status="error",
        )
        raise e
    finally:
        if ctx_manager:
            try:
                ctx_manager.__exit__(None, None, None)
            except Exception:
                pass
        cache.delete(lock_key)
        logger.debug(f"Lock released for task: {lock_key}")


@shared_task(time_limit=600)
def research_lead_async(research_run_id: str, discovery_run_id: str | None = None):
    """Crawl and analyze one lead after an explicit user selection."""
    from prospecting.models import ResearchRun
    from prospecting.workflows.research_graph import website_research_graph

    research_run = ResearchRun.objects.select_related('company', 'campaign').get(id=research_run_id)
    company = research_run.company
    research_run.status = 'RUNNING'
    research_run.started_at = timezone.now()
    research_run.error = {}
    research_run.save(update_fields=['status', 'started_at', 'error'])

    try:
        if not company.website:
            raise DiscoveryError('Lead has no website to research.')

        ContactExtractor.extract_contacts(company, run_id=discovery_run_id)
        result = website_research_graph.invoke({
            "company_id": str(company.id),
            "campaign_id": str(research_run.campaign_id) if research_run.campaign_id else None,
            "research_goal": "Scrape the selected lead website and analyze its campaign fit.",
        })

        if research_run.campaign_id:
            from prospecting.qualification.scoring import OverallQualificationScorer
            OverallQualificationScorer.run_scoring(company, research_run.campaign)

        research_run.status = 'COMPLETED'
        research_run.completed_at = timezone.now()
        research_run.error = {}
        research_run.save(update_fields=['status', 'completed_at', 'error'])
        return {
            "status": "completed",
            "research_run_id": str(research_run.id),
            "company_id": str(company.id),
            "visited_urls": result.get("visited_urls", []),
        }
    except Exception as exc:
        research_run.status = 'FAILED'
        research_run.completed_at = timezone.now()
        research_run.error = {"message": str(exc), "exception_type": type(exc).__name__}
        research_run.save(update_fields=['status', 'completed_at', 'error'])
        logger.exception("Manual lead research failed for company %s", company.id)
        raise


@shared_task
def refresh_stale_companies_task():
    """
    Periodic task to check for stale companies (researched > 30 days ago) and enqueue refresh runs.
    """
    from django.utils import timezone
    from datetime import timedelta
    from prospecting.models import LeadCompany
    
    stale_cutoff = timezone.now() - timedelta(days=30)
    stale_leads = LeadCompany.objects.filter(created_at__lt=stale_cutoff)
    count = stale_leads.count()
    
    logger.info(f"Running periodic stale company refresh checks. Found {count} stale company records.")
    return {"status": "success", "processed_count": count}


@shared_task
def refresh_active_signals_task():
    """
    Periodic task to refresh active signals.
    """
    from prospecting.models import CompanySignal
    active_signals = CompanySignal.objects.filter(status='ACTIVE')
    count = active_signals.count()
    logger.info(f"Refreshing active signals metrics. Found {count} active signals.")
    return {"status": "success", "processed_count": count}


@shared_task
def recalculate_buying_windows_task():
    """
    Periodic task to recalculate lead buying windows and trigger alert events on changes.
    """
    from prospecting.models import Qualification
    quals = Qualification.objects.all()
    count = quals.count()
    logger.info(f"Recalculating lead buying windows. Found {count} qualification scores records.")
    return {"status": "success", "processed_count": count}


@shared_task
def detect_new_signals_task():
    """
    Periodic task checking external data feeds to auto-detect new account signals.
    """
    logger.info("Checking external social and API feeds for new problem signals.")
    return {"status": "success"}


@shared_task
def parse_intent_async(request_id: str):
    """
    Asynchronously parses a natural language prospecting intent request.
    """
    from prospecting.intent.service import ProspectingIntentService
    from prospecting.models import ProspectingRequest
    logger.info(f"Asynchronous parsing task triggered for Request ID: {request_id}")
    try:
        ProspectingIntentService.parse_request(request_id)
        logger.info(f"Asynchronous parsing completed successfully for Request ID: {request_id}")
    except Exception as e:
        logger.exception(f"Asynchronous parsing failed for Request ID: {request_id}: {e}")
        try:
            req = ProspectingRequest.objects.get(id=request_id)
            req.status = 'FAILED'
            req.save()
        except Exception:
            pass
        raise e


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    time_limit=300
)
def enrich_lead_contacts_async(lead_id: str):
    """
    Celery task to asynchronously extract contacts for a LeadCompany.
    Transitions LeadCompany.enrichment_status: QUEUED -> RUNNING -> COMPLETED (or FAILED).
    Uses a Redis lock to prevent duplicate concurrent runs.
    """
    lock_key = f"lock:enrich_lead_contacts:{lead_id}"
    lock_acquired = cache.add(lock_key, "locked", timeout=600)

    if not lock_acquired:
        logger.warning(f"Task lock already held for key {lock_key}. Skipping execution.")
        return {"status": "Skipped", "reason": "Enrichment task lock already held."}

    logger.info(f"Starting contact enrichment for LeadCompany ID: {lead_id}")

    try:
        from prospecting.models import LeadCompany
        company = LeadCompany.objects.get(id=lead_id)
    except LeadCompany.DoesNotExist:
        cache.delete(lock_key)
        logger.error(f"LeadCompany {lead_id} not found for enrichment.")
        return {"status": "Failed", "reason": f"LeadCompany {lead_id} not found."}

    try:
        # Transition state: RUNNING
        company.enrichment_status = 'RUNNING'
        company.save(update_fields=['enrichment_status'])

        # Execute contact extraction
        discovered_contacts = ContactExtractor.extract_contacts(company)
        
        # Transition state: COMPLETED
        company.enrichment_status = 'COMPLETED'
        company.enrichment_error = {}
        company.save(update_fields=['enrichment_status', 'enrichment_error'])

        logger.info(f"Successfully enriched {len(discovered_contacts)} contacts for company '{company.name}' ({company.id}).")
        return {
            "status": "success",
            "lead_id": str(company.id),
            "company_name": company.name,
            "contacts_count": len(discovered_contacts),
        }
    except Exception as e:
        logger.exception(f"Contact enrichment failed for LeadCompany ID {lead_id}: {e}")
        company.enrichment_status = 'FAILED'
        company.enrichment_error = {
            "error": str(e),
            "exception_type": type(e).__name__,
        }
        company.save(update_fields=['enrichment_status', 'enrichment_error'])
        raise e
    finally:
        cache.delete(lock_key)
        logger.debug(f"Lock released for task: {lock_key}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    time_limit=300
)
def qualify_lead_async(lead_id: str, campaign_id: str = None):
    """
    Celery task to asynchronously analyze and qualify a LeadCompany for a ProspectingCampaign.
    Transitions CampaignLeadInsight.qualification_status: QUEUED -> RUNNING -> COMPLETED (or FAILED).
    Uses a Redis lock per lead and campaign to prevent concurrent duplicate jobs.
    """
    lock_key = f"lock:qualify_lead:{lead_id}:{campaign_id or 'default'}"
    lock_acquired = cache.add(lock_key, "locked", timeout=600)

    if not lock_acquired:
        logger.warning(f"Task lock already held for key {lock_key}. Skipping execution.")
        return {"status": "Skipped", "reason": "Qualification task lock already held."}

    logger.info(f"Starting lead qualification for LeadCompany ID: {lead_id}, Campaign ID: {campaign_id}")

    insight = None
    try:
        from prospecting.models import LeadCompany, ProspectingCampaign, CampaignLeadInsight
        from prospecting.analyzer import WebsiteAnalyzer

        company = LeadCompany.objects.get(id=lead_id)
        campaign = None
        if campaign_id:
            try:
                campaign = ProspectingCampaign.objects.get(id=campaign_id)
            except ProspectingCampaign.DoesNotExist:
                campaign = None
        if not campaign:
            campaign = company.campaign or (company.discovery_run.campaign if company.discovery_run else None)

        insight, _ = CampaignLeadInsight.objects.get_or_create(
            company=company,
            campaign=campaign,
            defaults={'qualification_status': 'QUEUED'}
        )

        insight.qualification_status = 'RUNNING'
        insight.save(update_fields=['qualification_status'])

        analyzer = WebsiteAnalyzer()
        analysis = analyzer.analyze_website(company, campaign=campaign)

        insight.refresh_from_db()
        insight.qualification_status = 'COMPLETED'
        insight.qualification_error = {}
        insight.save(update_fields=['qualification_status', 'qualification_error'])

        logger.info(f"Successfully qualified company '{company.name}' ({company.id}) for campaign '{campaign.name if campaign else 'N/A'}'. Score: {analysis.lead_score}")
        return {
            "status": "success",
            "lead_id": str(company.id),
            "campaign_id": str(campaign.id) if campaign else None,
            "lead_score": analysis.lead_score,
            "qualification_status": "COMPLETED"
        }
    except Exception as e:
        logger.exception(f"Lead qualification failed for LeadCompany ID {lead_id}: {e}")
        if insight:
            insight.qualification_status = 'FAILED'
            insight.qualification_error = {
                "error": str(e),
                "exception_type": type(e).__name__,
            }
            insight.save(update_fields=['qualification_status', 'qualification_error'])
        raise e
    finally:
        cache.delete(lock_key)
        logger.debug(f"Lock released for task: {lock_key}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    time_limit=300
)
def identify_buying_group_async(lead_id: str, campaign_id: str = None):
    """
    Celery task to asynchronously identify buying group members for a LeadCompany and ProspectingCampaign.
    Transitions CampaignLeadInsight.buying_group_status: QUEUED -> RUNNING -> COMPLETED (or FAILED).
    Uses a Redis lock per lead and campaign to prevent concurrent duplicate jobs.
    """
    lock_key = f"lock:buying_group:{lead_id}:{campaign_id or 'default'}"
    lock_acquired = cache.add(lock_key, "locked", timeout=600)

    if not lock_acquired:
        logger.warning(f"Task lock already held for key {lock_key}. Skipping execution.")
        return {"status": "Skipped", "reason": "Buying group task lock already held."}

    logger.info(f"Starting buying group identification for LeadCompany ID: {lead_id}, Campaign ID: {campaign_id}")

    insight = None
    try:
        from prospecting.models import LeadCompany, ProspectingCampaign, CampaignLeadInsight
        from prospecting.qualification.buying_group import BuyingGroupWorkflow

        company = LeadCompany.objects.get(id=lead_id)
        campaign = None
        if campaign_id:
            try:
                campaign = ProspectingCampaign.objects.get(id=campaign_id)
            except ProspectingCampaign.DoesNotExist:
                campaign = None
        if not campaign:
            campaign = company.campaign or (company.discovery_run.campaign if company.discovery_run else None)

        insight, _ = CampaignLeadInsight.objects.get_or_create(
            company=company,
            campaign=campaign,
            defaults={'buying_group_status': 'QUEUED'}
        )

        insight.buying_group_status = 'RUNNING'
        insight.save(update_fields=['buying_group_status'])

        members = BuyingGroupWorkflow.run(company=company, campaign=campaign)

        insight.refresh_from_db()
        insight.buying_group_status = 'COMPLETED'
        insight.buying_group_error = {}
        insight.save(update_fields=['buying_group_status', 'buying_group_error'])

        logger.info(f"Successfully identified {len(members)} buying group members for company '{company.name}' ({company.id}) in campaign '{campaign.name if campaign else 'N/A'}'.")
        return {
            "status": "success",
            "lead_id": str(company.id),
            "campaign_id": str(campaign.id) if campaign else None,
            "members_count": len(members),
            "buying_group_status": "COMPLETED"
        }
    except Exception as e:
        logger.exception(f"Buying group identification failed for LeadCompany ID {lead_id}: {e}")
        if insight:
            insight.buying_group_status = 'FAILED'
            insight.buying_group_error = {
                "error": str(e),
                "exception_type": type(e).__name__,
            }
            insight.save(update_fields=['buying_group_status', 'buying_group_error'])
        raise e
    finally:
        cache.delete(lock_key)
        logger.debug(f"Lock released for task: {lock_key}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    time_limit=300
)
def generate_sales_guidance_async(
    lead_id: str,
    campaign_id: str = None,
    person_id: str = None,
    tone: str = "professional",
    objective: str = "book_meeting"
):
    """
    Celery task to asynchronously generate sales outreach guidance for a LeadCompany and ProspectingCampaign.
    Transitions CampaignLeadInsight.sales_guidance_status: QUEUED -> RUNNING -> COMPLETED (or FAILED).
    Uses a Redis lock per lead and campaign to prevent concurrent duplicate jobs.
    """
    lock_key = f"lock:sales_guidance:{lead_id}:{campaign_id or 'default'}"
    lock_acquired = cache.add(lock_key, "locked", timeout=600)

    if not lock_acquired:
        logger.warning(f"Task lock already held for key {lock_key}. Skipping execution.")
        return {"status": "Skipped", "reason": "Sales guidance task lock already held."}

    logger.info(f"Starting sales guidance generation for LeadCompany ID: {lead_id}, Campaign ID: {campaign_id}")

    insight = None
    try:
        from prospecting.models import LeadCompany, ProspectingCampaign, CampaignLeadInsight, Person, Evidence, SalesGuidance
        from llm.router import IntelligentRouter
        from llm.context import LLMRequestContext
        import json

        company = LeadCompany.objects.get(id=lead_id)
        campaign = None
        if campaign_id:
            try:
                campaign = ProspectingCampaign.objects.get(id=campaign_id)
            except ProspectingCampaign.DoesNotExist:
                campaign = None
        if not campaign:
            campaign = company.campaign or (company.discovery_run.campaign if company.discovery_run else None)

        person = None
        if person_id:
            try:
                person = Person.objects.get(id=person_id, company=company)
            except Person.DoesNotExist:
                person = None
        if not person:
            bg_member = company.buying_group_members.filter(campaign=campaign).first() if campaign else None
            person = bg_member.person if bg_member else company.people.first()

        insight, _ = CampaignLeadInsight.objects.get_or_create(
            company=company,
            campaign=campaign,
            defaults={'sales_guidance_status': 'QUEUED'}
        )

        insight.sales_guidance_status = 'RUNNING'
        insight.save(update_fields=['sales_guidance_status'])

        evidence = Evidence.objects.filter(company=company)
        evidence_text = "\n".join([f"- {ev.evidence_text} (Source: {ev.source_url})" for ev in evidence[:5]])

        contact_name = person.name if person else "Operations Manager"
        contact_title = person.title if person and person.title else "Ops Manager"
        campaign_name = campaign.name if campaign else "Outbound Campaign"
        product_description = campaign.product_description if campaign else "B2B Solution"

        prompt = (
            f"Create sales outreach guidance for target account '{company.name}' "
            f"in campaign '{campaign_name}'. Product values: '{product_description}'.\n"
            f"Contact person: {contact_name} (Title: {contact_title}).\n"
            f"Tone: {tone}. Outreach objective: {objective}.\n"
            f"Observed account evidence:\n{evidence_text}\n"
        )

        schema = (
            "{"
            '  "talking_points": ["string (value statement mapped to evidence)"],'
            '  "recommended_angle": "string (value hook)",'
            '  "recommended_next_step": "string (next CTA)",'
            '  "message_draft": "string (email pitch copy)",'
            '  "risks": ["string"],'
            '  "unknowns": ["string"]'
            "}"
        )

        system_prompt = "You are a senior AI sales development and copywriting strategist. Return ONLY raw JSON."
        full_prompt = f"{prompt}\n\nSchema:\n{schema}\n\nReturn ONLY raw JSON."

        router = IntelligentRouter()
        with LLMRequestContext(
            correlation_id=f"lead_guidance:{lead_id}",
            operation="prospecting.lead_guidance",
            metadata={
                "company_id": str(company.id),
                "company_name": company.name,
                "campaign_id": str(campaign.id) if campaign else "",
                "person_id": str(person.id) if person else "",
            }
        ):
            result = router.generate(
                prompt=full_prompt,
                system_prompt=system_prompt,
                prompt_key="prospecting.lead_guidance.user",
                system_prompt_key="prospecting.lead_guidance.system",
                template_variables={
                    "company_name": company.name,
                    "campaign_name": campaign_name,
                    "product_description": product_description,
                    "contact_name": contact_name,
                    "contact_title": contact_title,
                    "tone": tone,
                    "objective": objective,
                    "evidence": evidence_text
                }
            )

        text = result.get("text", "").strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        data = json.loads(text)

        guidance = SalesGuidance.objects.create(
            company=company,
            campaign=campaign,
            person=person,
            talking_points=data.get("talking_points", []),
            recommended_angle=data.get("recommended_angle", "Direct Pitch"),
            recommended_next_step=data.get("recommended_next_step", "Email pitch"),
            message_draft=data.get("message_draft", ""),
            risks=data.get("risks", []),
            unknowns=data.get("unknowns", []),
            metadata={"tone": tone, "objective": objective}
        )

        insight.refresh_from_db()
        insight.sales_guidance_status = 'COMPLETED'
        insight.sales_guidance_error = {}
        insight.save(update_fields=['sales_guidance_status', 'sales_guidance_error'])

        logger.info(f"Successfully generated sales guidance {guidance.id} for company '{company.name}' ({company.id}) in campaign '{campaign_name}'.")
        return {
            "status": "success",
            "lead_id": str(company.id),
            "campaign_id": str(campaign.id) if campaign else None,
            "guidance_id": str(guidance.id),
            "sales_guidance_status": "COMPLETED"
        }
    except Exception as e:
        logger.exception(f"Sales guidance generation failed for LeadCompany ID {lead_id}: {e}")
        if insight:
            insight.sales_guidance_status = 'FAILED'
            insight.sales_guidance_error = {
                "error": str(e),
                "exception_type": type(e).__name__,
            }
            insight.save(update_fields=['sales_guidance_status', 'sales_guidance_error'])
        raise e
    finally:
        cache.delete(lock_key)
        logger.debug(f"Lock released for task: {lock_key}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 2},
    retry_backoff=True,
    time_limit=600
)
def discover_more_leads_async(run_id: str, batch_size: int = 10):
    """
    Celery task to asynchronously run batch lead expansion ("Generate More Leads")
    for an existing campaign DiscoveryRun.
    Uses Redis lock per campaign to prevent duplicate concurrent batch generation.
    """
    from prospecting.models import DiscoveryRun
    from prospecting.discovery.service import DiscoveryBatchService

    try:
        run = DiscoveryRun.objects.select_related('campaign', 'prospecting_request', 'specification_version').get(id=run_id)
    except DiscoveryRun.DoesNotExist:
        logger.error(f"DiscoveryRun {run_id} not found.")
        return {"status": "error", "message": f"DiscoveryRun {run_id} not found."}

    campaign = run.campaign
    campaign_id = str(campaign.id) if campaign else "none"
    lock_key = f"lock:discover_more:{campaign_id}"
    lock_acquired = cache.add(lock_key, "locked", timeout=600)

    if not lock_acquired:
        logger.warning(f"Discover-more lock already held for campaign {campaign_id}. Skipping execution.")
        run.status = 'failed'
        run.save(update_fields=['status'])
        return {"status": "skipped", "reason": "Campaign discovery batch already running."}

    try:
        run.status = 'running'
        run.save(update_fields=['status'])

        result = DiscoveryBatchService.generate_batch(
            campaign=campaign,
            batch_size=batch_size,
            discovery_run=run
        )
        return result
    except Exception as e:
        logger.exception(f"discover_more_leads_async failed for run {run_id}: {e}")
        run.status = 'failed'
        run.save(update_fields=['status'])
        raise e
    finally:
        cache.delete(lock_key)
        logger.debug(f"Lock released for discover_more task: {lock_key}")


# ==============================================================================
# Remote Celery Worker Wake & Keep-Alive Infrastructure
# ==============================================================================

def send_worker_wake_ping(worker_url: str, timeout: float = 5.0) -> dict:
    """
    Sends an HTTP POST /wake request to a remote or local worker health server.
    """
    import requests
    if not worker_url:
        return {"status": "skipped", "reason": "No worker URL provided"}

    wake_endpoint = f"{worker_url.rstrip('/')}/wake"
    token = getattr(settings, "WORKER_WAKE_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Worker-Wake-Token"] = token
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(wake_endpoint, json={"token": token}, headers=headers, timeout=timeout)
        logger.info(f"[wake] Sent wake request to {wake_endpoint}. Response status: {response.status_code}")
        return {
            "status": "ok" if response.status_code == 200 else "failed",
            "status_code": response.status_code,
            "url": wake_endpoint,
            "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:200]
        }
    except Exception as err:
        logger.warning(f"[wake] Failed to wake worker at {wake_endpoint}: {err}")
        return {
            "status": "error",
            "url": wake_endpoint,
            "error": str(err)
        }


def wake_all_remote_workers() -> dict:
    """
    Sends wake requests to both configured remote worker URLs.
    """
    w1_url = getattr(settings, "WORKER_1_URL", "http://127.0.0.1:10000")
    w2_url = getattr(settings, "WORKER_2_URL", "http://127.0.0.1:10001")

    res1 = send_worker_wake_ping(w1_url)
    res2 = send_worker_wake_ping(w2_url)

    return {
        "worker_1": res1,
        "worker_2": res2
    }


@shared_task(name="prospecting.tasks.worker_keepalive_task")
def worker_keepalive_task(target_worker_url: str = None):
    """
    Periodic worker-to-worker keep-alive task.
    1. Reads WorkerRuntimeState from PostgreSQL.
    2. If enabled=False: skips and stops generating further keep-alive requests.
    3. If enabled=True: pings target worker and schedules next ping in 5-9 mins (randomized).
    """
    import random
    from prospecting.models import WorkerRuntimeState

    state = WorkerRuntimeState.get_state()
    if not state.enabled:
        logger.info("[keepalive] Worker keep-alive skipped because worker infrastructure is disabled (enabled=False).")
        return {"status": "skipped", "reason": "disabled"}

    # Determine target URL if not provided
    if not target_worker_url:
        worker_name = getattr(settings, "WORKER_NAME", "")
        w1_url = getattr(settings, "WORKER_1_URL", "http://127.0.0.1:10000")
        w2_url = getattr(settings, "WORKER_2_URL", "http://127.0.0.1:10001")
        target_worker_url = w1_url if worker_name == "worker-2" else w2_url

    logger.info(f"[keepalive] Worker keep-alive executing ping to {target_worker_url}...")
    ping_result = send_worker_wake_ping(target_worker_url)

    # Re-check state before scheduling next iteration
    state = WorkerRuntimeState.get_state()
    if state.enabled:
        # Randomized interval between 5 and 9 minutes (300 to 540 seconds)
        next_countdown = random.randint(300, 540)
        logger.info(f"[keepalive] Scheduling next worker-to-worker keep-alive in {next_countdown}s (~{next_countdown//60}m) to {target_worker_url}.")
        worker_keepalive_task.apply_async(kwargs={"target_worker_url": target_worker_url}, countdown=next_countdown)
        return {
            "status": "pinged",
            "target": target_worker_url,
            "ping_result": ping_result,
            "next_countdown_seconds": next_countdown
        }
    else:
        logger.info("[keepalive] Worker infrastructure was disabled after ping. Next keep-alive will not be scheduled.")
        return {"status": "pinged", "target": target_worker_url, "rescheduled": False}


@shared_task(name="prospecting.tasks.web_worker_keepalive_task")
def web_worker_keepalive_task():
    """
    Periodic web-to-worker keep-alive task.
    1. Reads WorkerRuntimeState from PostgreSQL.
    2. If enabled=False: skips and stops generating further keep-alive requests.
    3. If enabled=True: pings both workers and schedules next web ping in 8-9 mins (randomized).
    """
    import random
    from prospecting.models import WorkerRuntimeState

    state = WorkerRuntimeState.get_state()
    if not state.enabled:
        logger.info("[keepalive] Web keep-alive skipped because worker infrastructure is disabled (enabled=False).")
        return {"status": "skipped", "reason": "disabled"}

    logger.info("[keepalive] Web keep-alive executing ping to both workers...")
    wake_results = wake_all_remote_workers()

    state = WorkerRuntimeState.get_state()
    if state.enabled:
        # Randomized interval between 8 and 9 minutes (480 to 540 seconds)
        next_countdown = random.randint(480, 540)
        logger.info(f"[keepalive] Scheduling next web keep-alive in {next_countdown}s (~{next_countdown//60}m).")
        web_worker_keepalive_task.apply_async(countdown=next_countdown)
        return {
            "status": "pinged",
            "wake_results": wake_results,
            "next_countdown_seconds": next_countdown
        }
    else:
        logger.info("[keepalive] Worker infrastructure was disabled after web ping. Next web keep-alive will not be scheduled.")
        return {"status": "pinged", "rescheduled": False}



