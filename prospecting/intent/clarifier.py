from typing import List
from prospecting.intent.schemas import ProspectingSpecification

class ProspectingIntakeClarifier:
    """Determines if a ProspectingSpecification has critical missing fields that block execution."""

    @staticmethod
    def get_missing_fields(spec: ProspectingSpecification) -> List[str]:
        missing = []
        # Objective is required
        if not spec.objective.value.strip():
            missing.append("objective")
        # Target description is required
        if not spec.target.description.value.strip():
            missing.append("target_description")
        return missing
