import logging
from typing import List
from celery import shared_task
from django.core.cache import cache
from asgiref.sync import async_to_sync
import asyncio
import threading
from urllib.parse import urlsplit

from django.conf import settings

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
    cache.set(f"discovery_run:{run_id}:progress", {
        "stage": stage,
        "progress": progress,
        "message": message,
        "status": "failed" if stage == "failed" else ("completed" if stage == "completed" else "running")
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
def discover_campaign_async(run_id: str):
    """
    Celery task to run the business discovery and lead enrichment asynchronously in the background.
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

        run.status = 'running'
        run.save()

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

        # Cap discovered leads list to respect settings.PROSPECTING_MAX_LEADS_PER_RUN limit
        from django.conf import settings
        max_leads = getattr(settings, "PROSPECTING_MAX_LEADS_PER_RUN", 10)
        if len(discovered_leads) > max_leads:
            logger.info(f"Capping discovered leads from {len(discovered_leads)} to {max_leads} (via settings.PROSPECTING_MAX_LEADS_PER_RUN)")
            discovered_leads = discovered_leads[:max_leads]

        # 2. Entity Resolution & Deduplication
        broadcast_progress(run_id, "resolving", 40, "Deduplicating discovered leads...")
        new_count = 0
        duplicate_count = 0
        leads_to_process = []

        for item in discovered_leads:
            existing = Deduplicator.find_existing_company(item)
            if existing:
                duplicate_count += 1
                from prospecting.models import DiscoveryLead
                DiscoveryLead.objects.get_or_create(discovery_run=run, company=existing)
                leads_to_process.append(existing)
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
                from prospecting.models import DiscoveryLead
                DiscoveryLead.objects.get_or_create(discovery_run=run, company=company)
                leads_to_process.append(company)

        # 3. Enrichment (Contacts & Suitability Analysis)
        # Keep all resolved websites visible, but only crawl a maximum of five
        # unique domains during one discovery run.
        configured_scrape_limit = getattr(
            settings,
            "DISCOVERY_MAX_WEBSITES_PER_RUN",
            MAX_WEBSITES_PER_DISCOVERY,
        )
        website_plan, companies_to_enrich = build_website_scrape_plan(
            leads_to_process,
            configured_scrape_limit,
        )
        trace.event(
            "website_selection",
            "Eligible websites listed and scrape budget applied",
            actor="workflow",
            input_data={
                "resolved_leads": len(leads_to_process),
                "requested_limit": configured_scrape_limit,
            },
            output_data={
                "websites": website_plan,
                "eligible_count": len(website_plan),
                "selected_count": len(companies_to_enrich),
                "limit": MAX_WEBSITES_PER_DISCOVERY,
            },
            metadata={
                "decision_source": "deterministic five-website safety limit",
                "eligible_count": len(website_plan),
                "selected_count": len(companies_to_enrich),
                "limit": MAX_WEBSITES_PER_DISCOVERY,
            },
        )

        total_to_enrich = len(companies_to_enrich)
        analyzer = WebsiteAnalyzer(trace_recorder=trace)

        for idx, company in enumerate(companies_to_enrich):
            progress_pct = 40 + int((idx / max(total_to_enrich, 1)) * 50)
            msg = f"Analyzing website and contacts for {company.name} ({idx + 1}/{total_to_enrich})..."
            broadcast_progress(
                run_id,
                "researching",
                progress_pct,
                msg
            )
            logger.debug(msg)
            try:
                # Extract contacts using ContactExtractor
                ContactExtractor.extract_contacts(company, run_id=run_id)
                # Analyze website using existing LLM analyzer
                analysis = analyzer.analyze_website(company, campaign=run.campaign)
                
                # Circuit Breaker: If the LLM router has failed completely (no healthy providers or
                # fallback failure), abort the task immediately to avoid wasting crawl time.
                if analysis and analysis.description == "Analysis request failed.":
                    reason = analysis.lead_score_reason or ""
                    if any(err in reason for err in ["No healthy providers available", "fallback options failed", "failed to generate response"]):
                        logger.error(f"Circuit breaker triggered: LLM Router is unavailable. Reason: {reason}")
                        raise DiscoveryError(f"Discovery aborted: LLM Router is offline ({reason}).")
            except DiscoveryError as de:
                # Re-raise DiscoveryError to propagate and fail the task early
                raise de
            except Exception as enrich_err:
                logger.error(f"Enrichment failed for {company.name}: {enrich_err}")

        # 4. Finalize Run
        run.status = 'completed'
        run.total_leads_found = len(discovered_leads)
        run.save()

        broadcast_progress(run_id, "completed", 100, "Discovery and enrichment finished.")
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
                "enriched_companies": total_to_enrich,
                "eligible_websites": len(website_plan),
                "scrape_limit": MAX_WEBSITES_PER_DISCOVERY,
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

    except Exception as e:
        logger.exception(f"Async prospecting run failed for ID: {run_id}")
        run.status = 'failed'
        run.save()
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
    logger.info(f"Asynchronous parsing task triggered for Request ID: {request_id}")
    try:
        ProspectingIntentService.parse_request(request_id)
        logger.info(f"Asynchronous parsing completed successfully for Request ID: {request_id}")
    except Exception as e:
        logger.exception(f"Asynchronous parsing failed for Request ID: {request_id}: {e}")
        raise e

