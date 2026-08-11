import json
import logging
from typing import Dict, Any
from knowledge_base.models import UserProfile, JobPosting
from llm.router import IntelligentRouter

logger = logging.getLogger(__name__)

JOB_PARSE_SYSTEM_PROMPT = """
You are an expert ATS & Job Parsing AI. Analyze raw job description text and extract structured JSON metadata.

Extract the following JSON structure:
{
  "company_name": "Google",
  "job_title": "Senior AI Infrastructure Engineer",
  "location": "Mountain View, CA (Hybrid)",
  "parsed_summary": "Building next-generation distributed AI training frameworks and agent runtime environments.",
  "required_skills": ["Python", "C++", "PyTorch", "Kubernetes", "Distributed Systems"],
  "preferred_skills": ["CUDA", "Triton", "Ray", "GCP"],
  "responsibilities": [
    "Design and implement high-performance model parallelization pipelines",
    "Optimize GPU cluster utilization for large-scale LLM training"
  ],
  "ats_keywords": ["PyTorch", "Distributed Systems", "Kubernetes", "GPU", "CUDA", "LLM Runtime", "Python"],
  "experience_years_required": 5
}
"""

class JobParser:
    """Parses raw job descriptions into structured JobPosting records."""

    def __init__(self, provider=None):
        self.provider = provider or IntelligentRouter()

    def parse_and_save(self, user_profile: UserProfile, raw_job_text: str, job_url: str = None) -> JobPosting:
        """Parse raw job text using LLM and create a JobPosting database record."""
        prompt = f"Please parse the following job description:\n\n{raw_job_text[:15000]}"
        
        result = self.provider.generate(
            prompt=prompt,
            system_prompt=JOB_PARSE_SYSTEM_PROMPT
        )
        
        if result.get("type") == "error":
            raise Exception(f"Job parsing failed: {result.get('text')}")
            
        raw_text = result.get("text", "")
        try:
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
                
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Job JSON: {e}")
            raise Exception("Invalid JSON returned by Job Parser LLM.")

        job_posting = JobPosting.objects.create(
            user_profile=user_profile,
            company_name=data.get("company_name", "Target Company"),
            job_title=data.get("job_title", "Software Engineer"),
            location=data.get("location", "Remote"),
            job_url=job_url,
            raw_text=raw_job_text,
            parsed_summary=data.get("parsed_summary", ""),
            required_skills=data.get("required_skills", []),
            preferred_skills=data.get("preferred_skills", []),
            responsibilities=data.get("responsibilities", []),
            ats_keywords=data.get("ats_keywords", []),
            experience_years_required=data.get("experience_years_required", 0)
        )
        return job_posting
