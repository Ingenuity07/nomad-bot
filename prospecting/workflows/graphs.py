import json
import logging
from typing import Dict, Any, List
from langgraph.graph import StateGraph, START, END
from llm.router import IntelligentRouter
from prospecting.models import ProspectingCampaign, ICPProfile, ProblemSignal, get_default_workspace
from prospecting.workflows.state import StrategyGraphState

logger = logging.getLogger(__name__)
router = IntelligentRouter()

def generate_structured(prompt: str, json_schema_desc: str) -> dict:
    """Helper to call LLM router and force json parsing response."""
    system_prompt = "You are a senior AI business growth and prospecting intelligence systems analyst. Return ONLY structured raw JSON."
    full_prompt = (
        f"{prompt}\n\n"
        f"You MUST return a JSON object matching this schema:\n"
        f"{json_schema_desc}\n\n"
        f"Return ONLY raw JSON. No Markdown formatting, code blocks or code fences."
    )
    result = router.generate(prompt=full_prompt, system_prompt=system_prompt)
    text = result.get("text", "").strip()
    
    # Strip markdown block fences
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
        logger.error(f"Failed parsing JSON from: '{text}'. Error: {e}")
        return {}


# 1. Product Analysis Node
def understand_product_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: understand_product")
    prompt = (
        f"Analyze this raw product description and locate target values:\n"
        f"'{state['input_description']}'"
    )
    schema = (
        "{"
        '  "product_name": "string",'
        '  "core_capabilities": ["string"],'
        '  "value_proposition": "string"'
        "}"
    )
    res = generate_structured(prompt, schema)
    return {"product_model": res}


# 2. Problem Decomposition Node
def decompose_problem_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: decompose_problem")
    prompt = (
        f"Given this product model: {state['product_model']},\n"
        f"identify core business problems, operational inefficiencies or buying indicators solved by it."
    )
    schema = (
        "{"
        '  "problems": ['
        "    {"
        '      "problem_name": "string",'
        '      "symptoms": ["string"],'
        '      "business_impact": "string"'
        "    }"
        "  ]"
        "}"
    )
    res = generate_structured(prompt, schema)
    return {"problem_model": res}


# 3. Customer Hypotheses Node
def generate_customer_hypotheses_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: generate_customer_hypotheses")
    prompt = (
        f"Using product capabilities {state['product_model']} and customer problems {state['problem_model']},\n"
        f"hypothesize target industries, ideal company sizes, and target roles."
    )
    schema = (
        "{"
        '  "hypotheses": ['
        "    {"
        '      "industry": "string",'
        '      "company_size": "string",'
        '      "target_roles": ["string"],'
        '      "rationale": "string"'
        "    }"
        "  ]"
        "}"
    )
    res = generate_structured(prompt, schema)
    
    # Collect flat lists from hypotheses
    target_roles = []
    for h in res.get("hypotheses", []):
        target_roles.extend(h.get("target_roles", []))
    
    return {
        "customer_hypotheses": res.get("hypotheses", []),
        "target_roles": list(set(target_roles))
    }


# 4. Signals Formulation Node
def generate_signals_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: generate_signals")
    prompt = (
        f"Formulate a list of observable signals (hiring, expansion, technology used, etc.) "
        f"we can find on websites or directories to confirm they have these problems: {state['problem_model']}."
    )
    schema = (
        "{"
        '  "signals": ['
        "    {"
        '      "name": "string",'
        '      "category": "string (e.g. HIRING, EXPANSION, FLEET_GROWTH)",'
        '      "description": "string",'
        '      "signal_type": "string (e.g. website_keyword, hiring_board)",'
        '      "detection_method": "string (e.g. scraper, contact_page)",'
        '      "weight": 1.0'
        "    }"
        "  ]"
        "}"
    )
    res = generate_structured(prompt, schema)
    return {"signals": res.get("signals", [])}


# 5. Search Strategy Generation Node
def generate_search_strategy_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: generate_search_strategy")
    prompt = (
        f"Generate optimized Google Places or Apify search terms to locate businesses "
        f"fitting these target profiles: {state['customer_hypotheses']}."
    )
    schema = (
        "{"
        '  "search_queries": ['
        "    {"
        '      "industry": "string",'
        '      "query": "string",'
        '      "location": "string",'
        '      "priority": 1,'
        '      "reason": "string"'
        "    }"
        "  ]"
        "}"
    )
    res = generate_structured(prompt, schema)
    return {"search_queries": res.get("search_queries", [])}


# 6. Deterministic Validation Node
def validate_strategy_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: validate_strategy")
    errors = []
    if not state.get("search_queries"):
        errors.append("No search strategy terms generated.")
    if not state.get("signals"):
        errors.append("No business discovery signals identified.")
    return {"validation_errors": errors}


# 7. Persist Node
def persist_icp_node(state: StrategyGraphState) -> Dict[str, Any]:
    logger.info("LangGraph Node: persist_icp")
    campaign_id = state.get("campaign_id")
    if not campaign_id:
        return {}

    try:
        campaign = ProspectingCampaign.objects.get(id=campaign_id)
        
        # Save or update ICP Profile record
        icp_profile, _ = ICPProfile.objects.update_or_create(
            campaign=campaign,
            version=1,
            defaults={
                "industries": [h.get("industry") for h in state.get("customer_hypotheses", [])],
                "company_sizes": [h.get("company_size") for h in state.get("customer_hypotheses", [])],
                "required_signals": [s.get("name") for s in state.get("signals", [])],
                "target_roles": state.get("target_roles", []),
                "search_terms": [q.get("query") for q in state.get("search_queries", [])],
                "generated_by_model": "langgraph-strategy"
            }
        )

        # Save formulated Signals
        workspace = get_default_workspace()
        for sig in state.get("signals", []):
            ProblemSignal.objects.update_or_create(
                workspace=workspace,
                name=sig.get("name"),
                defaults={
                    "category": sig.get("category", "OTHER"),
                    "description": sig.get("description", "Auto-generated signal"),
                    "signal_type": sig.get("signal_type", "website"),
                    "detection_method": sig.get("detection_method", "scraper"),
                    "weight": sig.get("weight", 1.0),
                    "active": True
                }
            )

    except Exception as e:
        logger.error(f"Error persisting ICP profile: {e}")

    return {}


# Compile state graph
workflow = StateGraph(StrategyGraphState)

workflow.add_node("understand_product", understand_product_node)
workflow.add_node("decompose_problem", decompose_problem_node)
workflow.add_node("generate_customer_hypotheses", generate_customer_hypotheses_node)
workflow.add_node("generate_signals", generate_signals_node)
workflow.add_node("generate_search_strategy", generate_search_strategy_node)
workflow.add_node("validate_strategy", validate_strategy_node)
workflow.add_node("persist_icp", persist_icp_node)

workflow.add_edge(START, "understand_product")
workflow.add_edge("understand_product", "decompose_problem")
workflow.add_edge("decompose_problem", "generate_customer_hypotheses")
workflow.add_edge("generate_customer_hypotheses", "generate_signals")
workflow.add_edge("generate_signals", "generate_search_strategy")
workflow.add_edge("generate_search_strategy", "validate_strategy")
workflow.add_edge("validate_strategy", "persist_icp")
workflow.add_edge("persist_icp", END)

strategy_formulator_graph = workflow.compile()
