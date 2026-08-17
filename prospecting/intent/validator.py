from typing import List, Dict, Any
from prospecting.intent.schemas import ProspectingSpecification, ObjectiveType

class SpecificationValidationError(Exception):
    """Exception raised when a prospecting specification fails business rule validation."""
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))

class ProspectingSpecificationValidator:
    """Performs deterministic validation on ProspectingSpecification models."""

    @staticmethod
    def validate_specification(spec: ProspectingSpecification) -> List[str]:
        errors = []

        # 1. Validate Objective Type
        obj_type_val = spec.objective_type.value
        try:
            ObjectiveType(obj_type_val)
        except ValueError:
            errors.append(f"Objective type '{obj_type_val}' is invalid. Must be one of {[ot.value for ot in ObjectiveType]}.")

        # 2. Validate Target Entity Type
        entity_type_val = spec.target.entity_type.value
        valid_entities = {"COMPANY", "PERSON", "ORGANIZATION", "LOCATION", "OTHER"}
        if entity_type_val not in valid_entities:
            errors.append(f"Target entity type '{entity_type_val}' is invalid. Must be one of {valid_entities}.")

        # 3. Validate Employee Range
        min_emp = spec.company_constraints.min_employees.value
        max_emp = spec.company_constraints.max_employees.value
        if min_emp is not None and max_emp is not None:
            if min_emp < 0:
                errors.append("min_employees cannot be negative.")
            if max_emp < 0:
                errors.append("max_employees cannot be negative.")
            if min_emp > max_emp:
                errors.append(f"min_employees ({min_emp}) cannot be greater than max_employees ({max_emp}).")

        # 4. Validate Revenue Range
        min_rev = spec.company_constraints.min_revenue.value
        max_rev = spec.company_constraints.max_revenue.value
        if min_rev is not None and max_rev is not None:
            if min_rev < 0:
                errors.append("min_revenue cannot be negative.")
            if max_rev < 0:
                errors.append("max_revenue cannot be negative.")
            if min_rev > max_rev:
                errors.append(f"min_revenue ({min_rev}) cannot be greater than max_revenue ({max_rev}).")

        # 5. Validate Geography direct contradiction checks
        countries = spec.geography.countries.value
        exclusions = spec.exclusion_rules.value
        for c in countries:
            for excl in exclusions:
                if c.lower().strip() == excl.lower().strip():
                    errors.append(f"Geography contradiction: '{c}' cannot be target country and excluded rule simultaneously.")

        return errors
