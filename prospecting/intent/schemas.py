from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class ObjectiveType(str, Enum):
    SELL = "SELL"
    SERVICE = "SERVICE"
    PARTNERSHIP = "PARTNERSHIP"
    SUPPLIER_SEARCH = "SUPPLIER_SEARCH"
    RECRUITING = "RECRUITING"
    MARKET_RESEARCH = "MARKET_RESEARCH"
    COMPETITIVE_RESEARCH = "COMPETITIVE_RESEARCH"
    INVESTMENT_RESEARCH = "INVESTMENT_RESEARCH"
    OTHER = "OTHER"

class Provenance(str, Enum):
    EXPLICIT_USER = "EXPLICIT_USER"
    LLM_INFERRED = "LLM_INFERRED"
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"
    USER_CONFIRMED = "USER_CONFIRMED"

class ProvenancedString(BaseModel):
    value: str
    provenance: Provenance = Provenance.SYSTEM_DEFAULT

class ProvenancedList(BaseModel):
    value: List[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.SYSTEM_DEFAULT

class ProvenancedInt(BaseModel):
    value: Optional[int] = None
    provenance: Provenance = Provenance.SYSTEM_DEFAULT

class TargetSpecification(BaseModel):
    entity_type: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="COMPANY", provenance=Provenance.SYSTEM_DEFAULT))
    description: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="", provenance=Provenance.SYSTEM_DEFAULT))
    industries: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    categories: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))

class ProblemHypothesisSpecification(BaseModel):
    problem: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="", provenance=Provenance.SYSTEM_DEFAULT))
    solution_or_offering: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="", provenance=Provenance.SYSTEM_DEFAULT))
    relationship: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="", provenance=Provenance.SYSTEM_DEFAULT))

class GeographySpecification(BaseModel):
    countries: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    regions: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    cities: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    radius: ProvenancedInt = Field(default_factory=lambda: ProvenancedInt(value=None, provenance=Provenance.SYSTEM_DEFAULT))
    scope: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="", provenance=Provenance.SYSTEM_DEFAULT))

class CompanyConstraintsSpecification(BaseModel):
    min_employees: ProvenancedInt = Field(default_factory=lambda: ProvenancedInt(value=None, provenance=Provenance.SYSTEM_DEFAULT))
    max_employees: ProvenancedInt = Field(default_factory=lambda: ProvenancedInt(value=None, provenance=Provenance.SYSTEM_DEFAULT))
    min_revenue: ProvenancedInt = Field(default_factory=lambda: ProvenancedInt(value=None, provenance=Provenance.SYSTEM_DEFAULT))
    max_revenue: ProvenancedInt = Field(default_factory=lambda: ProvenancedInt(value=None, provenance=Provenance.SYSTEM_DEFAULT))
    company_types: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))

class PeopleConstraintsSpecification(BaseModel):
    roles: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    departments: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    seniority: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    functions: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))

class ProspectingSpecification(BaseModel):
    objective_type: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="OTHER", provenance=Provenance.SYSTEM_DEFAULT))
    objective: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="", provenance=Provenance.SYSTEM_DEFAULT))
    target: TargetSpecification = Field(default_factory=TargetSpecification)
    problem_hypothesis: ProblemHypothesisSpecification = Field(default_factory=ProblemHypothesisSpecification)
    qualification_signals: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    geography: GeographySpecification = Field(default_factory=GeographySpecification)
    company_constraints: CompanyConstraintsSpecification = Field(default_factory=CompanyConstraintsSpecification)
    people_constraints: PeopleConstraintsSpecification = Field(default_factory=PeopleConstraintsSpecification)
    exclusion_rules: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    requested_information: ProvenancedList = Field(default_factory=lambda: ProvenancedList(value=[], provenance=Provenance.SYSTEM_DEFAULT))
    research_depth: ProvenancedString = Field(default_factory=lambda: ProvenancedString(value="standard", provenance=Provenance.SYSTEM_DEFAULT))

    def confirm_all_inferred(self):
        """Recursively update LLM_INFERRED fields to USER_CONFIRMED."""
        def confirm_field(obj: Any):
            if isinstance(obj, BaseModel):
                if hasattr(obj, 'provenance') and obj.provenance == Provenance.LLM_INFERRED:
                    obj.provenance = Provenance.USER_CONFIRMED
                for name in obj.model_fields:
                    confirm_field(getattr(obj, name))
            elif isinstance(obj, list):
                for item in obj:
                    confirm_field(item)
        confirm_field(self)

class IntentParseResult(BaseModel):
    status: str = Field(..., description="READY_FOR_REVIEW, NEEDS_CLARIFICATION, or INVALID")
    specification: ProspectingSpecification
    missing_information: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    parser_model: Optional[str] = None
    parser_provider: Optional[str] = None
