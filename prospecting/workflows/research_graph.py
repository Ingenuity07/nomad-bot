import re
import json
import logging
from typing import Dict, Any, List, TypedDict, Optional
from django.utils import timezone
from datetime import timedelta
from langgraph.graph import StateGraph, START, END
from llm.router import IntelligentRouter
from llm.tools.implementations.browser_tool import BrowserTool
from prospecting.models import LeadCompany, ProblemSignal, Evidence, CompanySignal, WebsiteAnalysis, get_default_workspace
from prospecting.discovery.normalizer import Normalizer

logger = logging.getLogger(__name__)
router = IntelligentRouter()
browser_tool = BrowserTool()

class ResearchGraphState(TypedDict):
    company_id: str
    campaign_id: Optional[str]
    research_goal: str
    visited_urls: List[str]
    candidate_urls: List[str]
    page_contents: Dict[str, str]
    findings: List[Dict[str, Any]]
    signals: List[Dict[str, Any]]
    evidence: List[Dict[str, Any]]
    next_action: str
    step_count: int
    token_usage: Dict[str, int]
    errors: List[str]
    final_summary: str

def generate_structured_research(prompt: str, json_schema_desc: str) -> dict:
    """Helper to query router and extract json block."""
    system_prompt = "You are an expert lead research agent. Analyze text contents and return ONLY structured JSON."
    full_prompt = (
        f"{prompt}\n\n"
        f"You MUST return a JSON object matching this schema:\n"
        f"{json_schema_desc}\n\n"
        f"Return ONLY raw JSON. Do not include markdown code fences or HTML tags."
    )
    result = router.generate(prompt=full_prompt, system_prompt=system_prompt)
    text = result.get("text", "").strip()
    
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except Exception as e:
        logger.error(f"Failed parsing research JSON: {e}")
        return {}

# 1. Load Context Node
def load_context_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: load_context")
    company_id = state.get("company_id")
    try:
        company = LeadCompany.objects.get(id=company_id)
        start_url = company.website if company.website else ""
        candidate_urls = [start_url] if start_url else []
        return {
            "visited_urls": [],
            "candidate_urls": candidate_urls,
            "page_contents": {},
            "findings": [],
            "signals": [],
            "evidence": [],
            "step_count": 0,
            "next_action": "continue",
            "errors": []
        }
    except LeadCompany.DoesNotExist:
        return {"next_action": "skip", "errors": ["Company not found"]}

# 2. Check Freshness Node
def check_freshness_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: check_freshness")
    company_id = state.get("company_id")
    try:
        # If company has website analysis updated in last 7 days, we skip new scrapes
        analysis = WebsiteAnalysis.objects.filter(company_id=company_id).first()
        if analysis and (timezone.now() - analysis.created_at) < timedelta(days=7):
            logger.info(f"Analysis for company {company_id} is fresh. Skipping new crawls.")
            return {"next_action": "skip"}
    except Exception as e:
        logger.warning(f"Error checking freshness: {e}")
    return {"next_action": "continue"}

# 3. Discover Pages Node
def discover_relevant_pages_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: discover_relevant_pages")
    visited = state.get("visited_urls", [])
    candidates = list(state.get("candidate_urls", []))
    
    # We only scan for links from homepage contents (first visited url)
    if not visited:
        return {}

    homepage_content = state.get("page_contents", {}).get(visited[0], "")
    if not homepage_content:
        return {}

    # Extract potential URLs from the page text using regex (simulating link harvesting)
    links = re.findall(r'href=["\'](https?://[^\s"\'>]+|/[^\s"\'>]+)', homepage_content)
    base_url = visited[0]
    
    keywords = ["career", "job", "service", "location", "about", "contact", "fleet", "routing", "delivery"]
    new_candidates = []

    for link in links:
        # Resolve relative links
        if link.startswith("/"):
            link = base_url.rstrip("/") + link
        
        # Only crawl links matching the same root domain
        if Normalizer.normalize_domain(link) == Normalizer.normalize_domain(base_url):
            if any(kw in link.lower() for kw in keywords):
                if link not in candidates and link not in new_candidates:
                    new_candidates.append(link)

    # Cap candidates to page budget of 4
    candidates.extend(new_candidates[:3])
    return {"candidate_urls": candidates[:4]}

# 4. Fetch Page Node
def fetch_page_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: fetch_page")
    candidates = state.get("candidate_urls", [])
    visited = list(state.get("visited_urls", []))
    page_contents = dict(state.get("page_contents", {}))

    # Find the next unvisited URL
    target_url = None
    for url in candidates:
        if url not in visited:
            target_url = url
            break

    if not target_url:
        return {}

    try:
        logger.info(f"BrowserTool navigating to target page: {target_url}")
        nav_res = browser_tool.execute(action="navigate", url=target_url)
        content_res = browser_tool.execute(action="get_content")
        
        page_contents[target_url] = content_res
        visited.append(target_url)
        
        return {
            "visited_urls": visited,
            "page_contents": page_contents,
            "step_count": state.get("step_count", 0) + 1
        }
    except Exception as e:
        logger.error(f"Error crawling URL '{target_url}': {e}")
        visited.append(target_url)  # mark visited to avoid stuck loops
        return {"visited_urls": visited, "errors": state.get("errors", []) + [str(e)]}

# 5. Extract Facts Node
def extract_facts_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: extract_facts")
    visited = state.get("visited_urls", [])
    if not visited:
        return {}

    last_url = visited[-1]
    content = state.get("page_contents", {}).get(last_url, "")
    if not content:
        return {}

    prompt = (
        f"Analyze this scraped website text page contents:\n"
        f"URL: {last_url}\n"
        f"Contents: '{content[:8000]}'\n\n"
        f"Extract key operational claims, services offered, driver hiring listings, scheduling language, "
        f"or service-area descriptions."
    )
    schema = (
        "{"
        '  "facts": ['
        "    {"
        '      "claim": "string (the direct operational assertion)",'
        '      "quoted_text": "string (exact matching quote from contents)",'
        '      "confidence": 1.0'
        "    }"
        "  ]"
        "}"
    )
    res = generate_structured_research(prompt, schema)
    
    findings = list(state.get("findings", []))
    evidence = list(state.get("evidence", []))

    for f in res.get("facts", []):
        f["source_url"] = last_url
        findings.append(f)
        if f.get("confidence", 0.0) >= 0.7:
            evidence.append(f)

    return {"findings": findings, "evidence": evidence}

# 6. Detect Signals Node
def detect_signals_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: detect_signals")
    evidence = state.get("evidence", [])
    
    # Simple logic mapping: evaluate whether any problems/signals are satisfied
    detected = []
    has_delivery = False
    has_scheduling = False
    needs_routing = False
    
    for ev in evidence:
        claim_lower = ev.get("claim", "").lower()
        if any(w in claim_lower for w in ["delivery", "courier", "shipping", "deliver"]):
            has_delivery = True
        if any(w in claim_lower for w in ["schedule", "appointment", "booking", "visit"]):
            has_scheduling = True
        if any(w in claim_lower for w in ["route", "optimization", "technician", "fleet", "driver"]):
            needs_routing = True

    if has_delivery:
        detected.append({"name": "Delivery Operations Detected", "category": "DELIVERY", "confidence": 0.9})
    if has_scheduling:
        detected.append({"name": "Customer Visits Scheduling Detected", "category": "SCHEDULING", "confidence": 0.8})
    if needs_routing:
        detected.append({"name": "Route Logistics Complexity Detected", "category": "ROUTING", "confidence": 0.85})

    return {"signals": detected}

# 7. Validate Evidence Node
def validate_evidence_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: validate_evidence")
    evidence = state.get("evidence", [])
    valid_evidence = []
    for ev in evidence:
        if ev.get("source_url") and ev.get("claim") and ev.get("quoted_text"):
            valid_evidence.append(ev)
    return {"evidence": valid_evidence}

# 8. Synthesize Account Node
def synthesize_account_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: synthesize_account")
    company_id = state.get("company_id")
    company = LeadCompany.objects.filter(id=company_id).first()
    company_name = company.name if company else "Target Company"
    
    prompt = (
        f"Based on these extracted evidence facts for business '{company_name}':\n"
        f"{state['evidence']}\n\n"
        f"Provide a concise explainable summary of what this company does and what operational logistics they manage."
    )
    res = router.generate(prompt=prompt, system_prompt="Synthesize research outcomes into a brief summary.")
    return {"final_summary": res.get("text", "No summary generated.")}

# 9. Persist Results Node
def persist_results_node(state: ResearchGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Research Node: persist_results")
    company_id = state.get("company_id")
    campaign_id = state.get("campaign_id")
    
    try:
        company = LeadCompany.objects.get(id=company_id)
        
        # Save WebsiteAnalysis
        has_delivery = False
        has_scheduling = False
        needs_routing = False
        
        for sig in state.get("signals", []):
            if sig.get("category") == "DELIVERY":
                has_delivery = True
            elif sig.get("category") == "SCHEDULING":
                has_scheduling = True
            elif sig.get("category") == "ROUTING":
                needs_routing = True

        # Calculate simple overall lead score (out of 10.0 scale)
        score_val = 2.0
        if has_delivery: score_val += 3.0
        if has_scheduling: score_val += 2.0
        if needs_routing: score_val += 3.0

        WebsiteAnalysis.objects.update_or_create(
            company=company,
            defaults={
                "description": state.get("final_summary", ""),
                "has_delivery": has_delivery,
                "has_scheduling": has_scheduling,
                "needs_routing": needs_routing,
                "lead_score": score_val,
                "lead_score_reason": f"Operational flags: Delivery={has_delivery}, Scheduling={has_scheduling}, Routing={needs_routing}."
            }
        )

        # Save Evidence models
        workspace = get_default_workspace()
        for ev in state.get("evidence", []):
            # Check or find corresponding problem signal
            sig_name = "Operational Signal"
            if "delivery" in ev.get("claim", "").lower():
                sig_name = "Delivery Operations Detected"
            elif "schedule" in ev.get("claim", "").lower():
                sig_name = "Customer Visits Scheduling Detected"

            prob_sig = ProblemSignal.objects.filter(workspace=workspace, name=sig_name).first()
            
            Evidence.objects.create(
                company=company,
                signal=prob_sig,
                source_type="website",
                source_url=ev.get("source_url"),
                source_title="Company Web Page",
                evidence_text=ev.get("quoted_text"),
                confidence=ev.get("confidence", 1.0)
            )

    except Exception as e:
        logger.error(f"Error saving website research results: {e}")

    return {}


# Router logic
def should_continue(state: ResearchGraphState):
    if state.get("next_action") == "skip":
        return "end"
    
    visited = state.get("visited_urls", [])
    candidates = state.get("candidate_urls", [])
    
    # Stop if visited max 4 pages or out of candidates
    unvisited = [u for u in candidates if u not in visited]
    if len(visited) >= 4 or not unvisited:
        return "finalize"
    
    return "fetch"

# Build State Graph
workflow = StateGraph(ResearchGraphState)

workflow.add_node("load_context", load_context_node)
workflow.add_node("check_freshness", check_freshness_node)
workflow.add_node("discover_relevant_pages", discover_relevant_pages_node)
workflow.add_node("fetch_page", fetch_page_node)
workflow.add_node("extract_facts", extract_facts_node)
workflow.add_node("detect_signals", detect_signals_node)
workflow.add_node("validate_evidence", validate_evidence_node)
workflow.add_node("synthesize_account", synthesize_account_node)
workflow.add_node("persist_results", persist_results_node)

# Connect edges
workflow.add_edge(START, "load_context")
workflow.add_edge("load_context", "check_freshness")

workflow.add_conditional_edges(
    "check_freshness",
    should_continue,
    {
        "end": END,
        "fetch": "fetch_page"
    }
)

workflow.add_edge("fetch_page", "extract_facts")
workflow.add_edge("extract_facts", "discover_relevant_pages")

workflow.add_conditional_edges(
    "discover_relevant_pages",
    should_continue,
    {
        "fetch": "fetch_page",
        "finalize": "detect_signals"
    }
)

workflow.add_edge("detect_signals", "validate_evidence")
workflow.add_edge("validate_evidence", "synthesize_account")
workflow.add_edge("synthesize_account", "persist_results")
workflow.add_edge("persist_results", END)

# Compile
website_research_graph = workflow.compile()
