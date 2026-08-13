from typing import TypedDict, List, Dict, Any, Optional

class StrategyGraphState(TypedDict):
    campaign_id: str
    input_description: str
    product_model: Dict[str, Any]
    problem_model: Dict[str, Any]
    customer_hypotheses: List[Dict[str, Any]]
    signals: List[Dict[str, Any]]
    search_queries: List[Dict[str, Any]]
    geographic_strategy: Dict[str, Any]
    target_roles: List[str]
    exclusions: List[str]
    validation_errors: List[str]
