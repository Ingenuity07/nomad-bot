from dataclasses import dataclass, field
from typing import Dict, Any, Optional

@dataclass
class ToolContext:
    run_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    source: str = "workflow"  # Possible values: workflow, agent, api, scheduled_task, test
    agent_name: Optional[str] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
