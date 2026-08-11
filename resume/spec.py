from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ResumeHeaderSpec(BaseModel):
    full_name: str
    headline: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None

class ResumeExperienceItemSpec(BaseModel):
    company: str
    role: str
    location: Optional[str] = None
    start_date: str
    end_date: str
    bullet_points: List[str]
    tech_stack: List[str] = Field(default_factory=list)

class ResumeProjectItemSpec(BaseModel):
    title: str
    description: str
    bullet_points: List[str]
    tech_stack: List[str] = Field(default_factory=list)
    project_url: Optional[str] = None

class ResumeSkillGroupSpec(BaseModel):
    category: str
    skills: List[str]

class StructuredResumeSpec(BaseModel):
    title: str = "Software Engineer Resume"
    header: ResumeHeaderSpec
    summary: str
    skills_groups: List[ResumeSkillGroupSpec]
    experiences: List[ResumeExperienceItemSpec]
    projects: List[ResumeProjectItemSpec]
    education: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
