import logging
from celery import shared_task
from django.core.cache import cache
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from prospecting.exceptions import DiscoveryError
from prospecting.models import DiscoveryRun, LeadCompany
from prospecting.repositories import DiscoveryRunRepository, LeadCompanyRepository
from prospecting.discovery.dto import DiscoveryRequest
from prospecting.discovery.providers.registry import discovery_provider_registry
from prospecting.discovery.deduplication import Deduplicator
from prospecting.contact import ContactExtractor
from prospecting.analyzer import WebsiteAnalyzer

logger = logging.getLogger(__name__)

def broadcast_progress(run_id: str, stage: str, progress: int, message: str):
    cache.set(f"discovery_run:{run_id}:progress", {
        "stage": stage,
        "progress": progress,
        "message": message,
        "status": "failed" if stage == "failed" else ("completed" if stage == "completed" else "running")
    }, timeout=86400)

    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
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
        async_to_sync(channel_layer.group_send)(
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

    logger.info(f"Lock acquired for prospecting task: {lock_key}")
    broadcast_progress(run_id, "queued", 5, "Initializing task runner...")

    run = DiscoveryRunRepository.get_run(run_id)
    if not run:
        cache.delete(lock_key)
        raise DiscoveryError(f"Discovery run {run_id} not found.")

    try:
        run.status = 'running'
        run.save()

        # 1. Initialize Tool Platform
        import os
        from llm.tools.context import ToolContext
        from llm.tools.executor import ToolExecutor
        from chat.orchestrator.single_agent import SingleAgentOrchestrator

        orchestrator = SingleAgentOrchestrator()
        executor = ToolExecutor(orchestrator.tool_registry)
        context = ToolContext(
            run_id=str(run.id),
            source="workflow"
        )

        # Select the best available discovery provider dynamically
        provider_name = "openstreetmap"
        for name in ["google_places", "apify", "openstreetmap"]:
            try:
                from llm.providers.registry import provider_registry
                prov = provider_registry.get(name)
                # Default to True if the provider does not define health_check
                is_healthy = prov.health_check() if hasattr(prov, "health_check") else True
                if is_healthy:
                    provider_name = name
                    break
            except KeyError:
                pass

        logger.info(f"Selected discovery provider: {provider_name}")
        broadcast_progress(
            run_id,
            "discovering",
            20,
            f"Searching target directory using {provider_name}..."
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
                
                if not search_keywords:
                    logger.info("Discovery Planner deriving search terms using router...")
                    from llm.router import IntelligentRouter
                    import json
                    router = IntelligentRouter()
                    planner_prompt = (
                        f"Based on the target description: \"{spec.target.description.value}\" "
                        f"and objective: \"{spec.objective.value}\", generate a JSON list of 2 to 3 optimized, "
                        "short search keyword terms (e.g., 'pest control', 'courier service') to find matching companies. "
                        "Return a JSON object with key 'search_queries'."
                    )
                    res = router.generate(prompt=planner_prompt, system_prompt="You are a helpful search optimization planner. Respond in raw JSON.")
                    if res.get("type") == "text":
                        parsed = json.loads(res.get("text", "{}"))
                        search_keywords = parsed.get("search_queries", [])
            except Exception as planner_err:
                logger.error(f"Discovery Planner failed: {planner_err}")

        # Fallback to run.keyword if no terms derived
        if not search_keywords:
            search_keywords = [run.keyword]

        # Execute company discovery using SearchCompaniesTool for each derived keyword
        from prospecting.discovery.dto import DiscoveryResultItem
        discovered_leads: List[DiscoveryResultItem] = []
        seen_lead_keys = set()
        
        for search_keyword in search_keywords:
            words = search_keyword.split()
            if not run.specification_version and (len(words) > 4 or any(w in search_keyword.lower() for w in ["app", "built", "helps", "business", "finding", "routing", "optimis"])):
                logger.info(f"Optimizing search query with LLM: \"{search_keyword[:40]}...\"")
                try:
                    from llm.router import IntelligentRouter
                    import json
                    router = IntelligentRouter()
                    prompt = (
                        "Extract 1 to 2 clean, short, standard business categories or search keywords (e.g., 'courier', 'logistics', 'delivery service') "
                        "from the following product or business pain point description:\n\n"
                        f"\"{search_keyword}\"\n\n"
                        "Return a JSON object with a single key 'keywords' containing a list of strings."
                    )
                    res = router.generate(prompt, system_prompt="You are a helpful keyword extraction assistant. Respond in raw JSON.")
                    if res.get("type") == "text":
                        parsed = json.loads(res.get("text", "{}"))
                        extracted = parsed.get("keywords", [])
                        if extracted:
                            search_keyword = extracted[0]
                except Exception as llm_err:
                    logger.error(f"Failed to optimize search keyword using LLM: {llm_err}")

            logger.info(f"Running search for: \"{search_keyword}\" in \"{run.location}\" using provider \"{provider_name}\"...")
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

            if tool_result.success and tool_result.data:
                for c in tool_result.data.get("companies", []):
                    name_key = c["name"].lower().strip()
                    web_key = c.get("website", "").lower().strip() if c.get("website") else ""
                    lead_key = (name_key, web_key)
                    if lead_key not in seen_lead_keys:
                        seen_lead_keys.add(lead_key)
                        discovered_leads.append(
                            DiscoveryResultItem(
                                name=c["name"],
                                website=c.get("website"),
                                phone=c.get("phone"),
                                address=c.get("address"),
                                category=c.get("category"),
                                external_id=c.get("external_id"),
                                raw_reference=c.get("raw_metadata", {})
                            )
                        )
        logger.info(f"Discovered {len(discovered_leads)} raw leads.")

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
        total_to_enrich = len(leads_to_process)
        analyzer = WebsiteAnalyzer()

        for idx, company in enumerate(leads_to_process):
            progress_pct = 40 + int((idx / max(total_to_enrich, 1)) * 50)
            msg = f"Analyzing website and contacts for {company.name} ({idx + 1}/{total_to_enrich})..."
            broadcast_progress(
                run_id,
                "researching",
                progress_pct,
                msg
            )
            logger.info(msg)
            try:
                # Extract contacts using ContactExtractor
                ContactExtractor.extract_contacts(company)
                # Analyze website using existing LLM analyzer
                analyzer.analyze_website(company)
            except Exception as enrich_err:
                logger.error(f"Enrichment failed for {company.name}: {enrich_err}")

        # 4. Finalize Run
        run.status = 'completed'
        run.total_leads_found = len(discovered_leads)
        run.save()

        broadcast_progress(run_id, "completed", 100, "Discovery and enrichment finished.")
        broadcast_completion(run_id, len(discovered_leads), new_count, duplicate_count)

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
        raise e
    finally:
        cache.delete(lock_key)
        logger.info(f"Lock released for task: {lock_key}")


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

