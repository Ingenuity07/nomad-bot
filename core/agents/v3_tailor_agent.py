import json
import time
import logging
from typing import Dict, Any
from memory.models import (
    UserProfile, ProfessionalKnowledgeBase, Experience, Project, Skill, JobPosting, ResumeVersion
)
from core.resume.latex_engine import LaTeXEngine
from core.jobs.ats_analyzer import ATSGapAnalyzer
from core.llm.router import IntelligentRouter

logger = logging.getLogger(__name__)

V3_TAILOR_SYSTEM_PROMPT = """
You are the Nomad V3 Resume Tailoring AI Agent.
Your core mandate is to create a Structured Resume Specification tailored to a specific target Job Posting using ONLY facts from the user's Professional Knowledge Base.

CRITICAL RULES:
1. NEVER invent companies, dates, skills, metrics, or experiences that are not present in the Knowledge Base.
2. Prioritize, reorder, and emphasize bullet points and projects that directly match the target Job Posting's ATS keywords.
3. Your output MUST be valid JSON adhering to the Structured Resume Specification schema:

{
  "title": "Tailored Resume for [Company]",
  "header": {
    "full_name": "Shivam Singh",
    "headline": "Senior Software Engineer | Backend & AI Infrastructure",
    "email": "shivam@example.com",
    "phone": "+1-555-0199",
    "location": "San Francisco, CA",
    "linkedin_url": "https://linkedin.com/in/shivam",
    "github_url": "https://github.com/Ingenuity07"
  },
  "summary": "Tailored professional summary emphasizing target company requirements...",
  "skills_groups": [
    {
      "category": "Languages & Frameworks",
      "skills": ["Python", "Django", "LangGraph", "FastAPI"]
    },
    {
      "category": "Cloud & Storage",
      "skills": ["PostgreSQL", "Redis", "Docker", "GCP"]
    }
  ],
  "experiences": [
    {
      "company": "Ridecell",
      "role": "Senior Software Engineer",
      "location": "San Francisco, CA",
      "start_date": "Jan 2022",
      "end_date": "Present",
      "bullet_points": [
        "Architected scalable microservices using Python and Redis...",
        "Engineered real-time telematics ingestion pipeline..."
      ],
      "tech_stack": ["Python", "Django", "Redis"]
    }
  ],
  "projects": [
    {
      "title": "Nomad Bot V3",
      "description": "Personal Career Operating System using AI agents and deterministic LaTeX rendering",
      "bullet_points": ["Built deterministic LaTeX resume generator and ATS gap analysis engine"],
      "tech_stack": ["Python", "Django", "PostgreSQL", "React"]
    }
  ]
}
"""

class V3TailorAgent:
    """
    Nomad V3 Tailoring Agent that compiles a tailored Structured Resume Spec
    and invokes the deterministic LaTeX Engine.
    """

    def __init__(self, provider=None):
        self.provider = provider or IntelligentRouter()

    def generate_tailored_resume(self, user_profile: UserProfile, job_posting: JobPosting, template_name: str = "modern") -> Dict[str, Any]:
        """Generate a tailored resume version from the Knowledge Base."""
        start_time = time.time()

        # Gather Knowledge Base context
        kb = getattr(user_profile, "knowledge_base", None)
        headline = kb.headline if kb else "Senior Software Engineer"
        summary = kb.summary if kb else ""
        
        experiences = list(Experience.objects.filter(user_profile=user_profile).values())
        projects = list(Project.objects.filter(user_profile=user_profile).values())
        skills = list(Skill.objects.filter(user_profile=user_profile).values())

        kb_context = {
            "profile": {
                "full_name": user_profile.full_name or user_profile.username,
                "headline": headline,
                "email": user_profile.email,
                "phone": user_profile.phone or "",
                "location": "San Francisco, CA",
                "linkedin_url": user_profile.linkedin_url or "",
                "github_url": user_profile.github_url or "",
                "portfolio_url": user_profile.portfolio_url or ""
            },
            "summary": summary,
            "experiences": experiences,
            "projects": projects,
            "skills": skills
        }

        job_context = {
            "company_name": job_posting.company_name,
            "job_title": job_posting.job_title,
            "required_skills": job_posting.required_skills,
            "preferred_skills": job_posting.preferred_skills,
            "ats_keywords": job_posting.ats_keywords,
            "responsibilities": job_posting.responsibilities
        }

        prompt = (
            f"Target Job Posting:\n{json.dumps(job_context, indent=2)}\n\n"
            f"User Professional Knowledge Base:\n{json.dumps(kb_context, indent=2, default=str)}\n\n"
            "Generate the tailored Structured Resume Specification JSON adhering strictly to the facts above."
        )

        result = self.provider.generate(
            prompt=prompt,
            system_prompt=V3_TAILOR_SYSTEM_PROMPT
        )

        if result.get("type") == "error":
            raise Exception(f"Tailoring agent failed: {result.get('text')}")

        raw_text = result.get("text", "")
        try:
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
            spec_data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode Tailor Agent spec JSON: {e}")
            raise Exception("Invalid Structured Spec JSON generated by Tailoring Agent.")

        # Pass Structured Spec to Deterministic LaTeX Generator using dynamic templates
        from core.resume.templates import ResumeTemplateRenderer, get_or_create_default_template
        from memory.models import ResumeTemplate

        template = ResumeTemplate.objects.filter(name=template_name, is_active=True).first()
        if not template:
            template = get_or_create_default_template()

        latex_code = ResumeTemplateRenderer.render(template, spec_data)
        pdf_path = LaTeXEngine.compile_pdf(latex_code)
        
        latency_ms = int((time.time() - start_time) * 1000)
        provider_name = result.get("provider", "router")

        # Save Immutable ResumeVersion
        version_name = f"{job_posting.company_name} - {job_posting.job_title} Resume"
        resume_version = ResumeVersion.objects.create(
            user_profile=user_profile,
            job_posting=job_posting,
            resume_template=template,
            version_name=version_name,
            template_name=template_name,
            structured_spec=spec_data,
            latex_code=latex_code,
            pdf_path=pdf_path,
            ats_score=0,
            generation_latency_ms=latency_ms,
            llm_provider=provider_name
        )

        # Run ATS Gap Analysis
        ats_data = ATSGapAnalyzer.analyze(user_profile, job_posting, resume_version=resume_version)

        return {
            "resume_version_id": str(resume_version.id),
            "version_name": version_name,
            "ats_score": ats_data["match_score"],
            "structured_spec": spec_data,
            "latex_code": latex_code,
            "pdf_path": pdf_path,
            "ats_report": ats_data,
            "generation_latency_ms": latency_ms
        }
