import json
import logging
from typing import Dict, Any, List
from knowledge_base.models import UserProfile, ProfessionalKnowledgeBase, Experience, Project, Skill
from knowledge_base.documents.loader import DocumentLoader
from knowledge_base.documents.ocr import OCRService
from knowledge_base.documents.detector import SectionDetector
from llm.router import IntelligentRouter

logger = logging.getLogger(__name__)

# Section-specific extraction prompts to optimize cost and accuracy
SUMMARY_PROMPT = """
You are an expert profile extraction AI. Parse the following resume summary section text and extract structured JSON:
{
  "title": "Senior Software Engineer",
  "headline": "Full-stack engineer specializing in AI agent frameworks and high-concurrency systems",
  "summary": "Professional summary paragraph...",
  "years_of_experience": 5,
  "target_roles": ["Senior Backend Engineer", "AI Systems Engineer"]
}
Only output valid JSON.
"""

EXPERIENCE_PROMPT = """
You are an expert work history extraction AI. Parse the following experience section text and extract a list of experiences:
[
  {
    "company": "Ridecell",
    "role": "Senior Software Engineer",
    "location": "San Francisco, CA",
    "start_date": "Jan 2022",
    "end_date": "Present",
    "is_current": true,
    "summary": "Leading backend infrastructure and caching optimizations",
    "bullet_points": [
      "Architected scalable microservices handling 10k req/sec",
      "Reduced deployment latency by 40% using automated CI/CD"
    ],
    "tech_stack": ["Python", "Django", "Redis", "Docker"]
  }
]
Only output valid JSON.
"""

PROJECTS_PROMPT = """
You are an expert project extraction AI. Parse the following projects section text and extract a list of projects:
[
  {
    "title": "Nomad Bot V3",
    "description": "Personal Career Operating System using AI agents and deterministic LaTeX rendering",
    "tech_stack": ["Python", "Django", "React", "PostgreSQL", "LangGraph"],
    "impact_metrics": ["Automated resume tailoring across 50+ applications"],
    "project_url": "https://github.com/Ingenuity07/nomad-bot"
  }
]
Only output valid JSON.
"""

SKILLS_PROMPT = """
You are an expert skills extraction AI. Parse the following skills section text and extract a list of skills:
[
  {"name": "Python", "category": "languages", "proficiency": "Expert"},
  {"name": "Django", "category": "frameworks", "proficiency": "Expert"},
  {"name": "PostgreSQL", "category": "databases", "proficiency": "Advanced"},
  {"name": "Docker", "category": "cloud_devops", "proficiency": "Advanced"}
]
Supported categories: "languages", "frameworks", "databases", "cloud_devops", "ai_ml", "tools", "concepts".
Only output valid JSON.
"""


class ResumeIngestionEngine:
    """Ingests resumes using a multi-stage loader, OCR, layout detector, and section extractor."""

    def __init__(self, provider=None):
        self.provider = provider or IntelligentRouter()
        self.ocr_service = OCRService(self.provider)

    def parse_file_path(self, user_profile: UserProfile, file_path: str) -> Dict[str, Any]:
        """Load a file from disk, run OCR if needed, parse sections, and ingest records."""
        # 1. Load File
        loaded = DocumentLoader.load(file_path)
        extracted_text = loaded["text"]
        
        # 2. Run OCR if necessary
        if not self.ocr_service.is_searchable(extracted_text):
            extracted_text = self.ocr_service.extract_via_ocr(file_path)
            
        return self.parse_and_ingest(user_profile, extracted_text)

    def parse_and_ingest(self, user_profile: UserProfile, raw_resume_text: str) -> Dict[str, Any]:
        """Parse raw resume text section-by-section and save to the Professional Knowledge Base."""
        # 3. Layout Parser & Section Detector
        sections = SectionDetector.detect_sections(raw_resume_text)
        
        # Extract Summary
        summary_data = self._extract_section_data(sections.get("summary", ""), SUMMARY_PROMPT, {
            "title": "Software Engineer",
            "headline": "",
            "summary": "",
            "years_of_experience": 0,
            "target_roles": []
        })
        
        # Extract Experiences
        experiences_data = self._extract_section_data(sections.get("experience", ""), EXPERIENCE_PROMPT, [])
        
        # Extract Projects
        projects_data = self._extract_section_data(sections.get("projects", ""), PROJECTS_PROMPT, [])
        
        # Extract Skills
        skills_data = self._extract_section_data(sections.get("skills", ""), SKILLS_PROMPT, [])

        # Update or Create ProfessionalKnowledgeBase record
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user_profile)
        kb.title = summary_data.get("title", "Software Engineer")
        kb.headline = summary_data.get("headline", "")
        kb.summary = summary_data.get("summary", "")
        kb.years_of_experience = summary_data.get("years_of_experience", 0)
        kb.target_roles = summary_data.get("target_roles", [])
        kb.save()

        # Save Experiences
        for idx, exp_data in enumerate(experiences_data):
            Experience.objects.create(
                user_profile=user_profile,
                company=exp_data.get("company", "Company"),
                role=exp_data.get("role", "Role"),
                location=exp_data.get("location", ""),
                start_date=exp_data.get("start_date", "2022"),
                end_date=exp_data.get("end_date", "Present"),
                is_current=exp_data.get("is_current", False),
                summary=exp_data.get("summary", ""),
                bullet_points=exp_data.get("bullet_points", []),
                tech_stack=exp_data.get("tech_stack", []),
                order=idx
            )

        # Save Projects
        for idx, proj_data in enumerate(projects_data):
            Project.objects.create(
                user_profile=user_profile,
                title=proj_data.get("title", "Project"),
                description=proj_data.get("description", ""),
                architecture_notes=proj_data.get("architecture_notes", ""),
                tech_stack=proj_data.get("tech_stack", []),
                impact_metrics=proj_data.get("impact_metrics", []),
                project_url=proj_data.get("project_url", ""),
                order=idx
            )

        # Save Skills
        for skill_data in skills_data:
            name = skill_data.get("name")
            if name:
                Skill.objects.update_or_create(
                    user_profile=user_profile,
                    name=name,
                    defaults={
                        "category": skill_data.get("category", "tools"),
                        "proficiency": skill_data.get("proficiency", "Expert")
                    }
                )

        return {
            "status": "success",
            "kb_id": str(kb.id),
            "experiences_created": len(experiences_data),
            "projects_created": len(projects_data),
            "skills_created": len(skills_data)
        }

    def _extract_section_data(self, section_text: str, system_prompt: str, default_val: Any) -> Any:
        """Call IntelligentRouter to extract structured JSON data from a specific resume section."""
        if not section_text.strip():
            return default_val
            
        prompt = f"Parse the following section text into structured JSON:\n\n{section_text}"
        result = self.provider.generate(
            prompt=prompt,
            system_prompt=system_prompt
        )
        
        if result.get("type") == "error":
            logger.warning(f"Section extraction failed: {result.get('text')}. Using default fallback.")
            return default_val
            
        raw_text = result.get("text", "")
        try:
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON for section: {e}. Raw text: {raw_text}")
            return default_val
