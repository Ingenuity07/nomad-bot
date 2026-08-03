import time
import logging
from typing import Dict, Any
from memory.models import UserProfile, JobPosting, ResumeVersion
from core.agents.v3_tailor_agent import V3TailorAgent
from core.agents.optimizer_agent import BulletOptimizerAgent
from core.jobs.ats_analyzer import ATSGapAnalyzer
from core.resume.latex_engine import LaTeXEngine
from core.resume.templates import ResumeTemplateRenderer, get_or_create_default_template
from memory.models import ResumeTemplate

logger = logging.getLogger(__name__)

class ResumeOptimizationPipeline:
    """Orchestrates the tailoring-evaluation-optimization loop for resume generation."""

    def __init__(self, provider=None):
        self.provider = provider
        self.tailor_agent = V3TailorAgent(provider)
        self.optimizer_agent = BulletOptimizerAgent(provider)

    def run(self, user_profile: UserProfile, job_posting: JobPosting, template_name: str = "modern") -> Dict[str, Any]:
        """Execute the optimization loop: Tailor -> Evaluate -> Optimize -> Compile."""
        start_time = time.time()

        # Step 1: Initial Tailoring Pass
        logger.info("Executing initial tailoring pass...")
        tailor_res = self.tailor_agent.generate_tailored_resume(user_profile, job_posting, template_name)
        
        initial_spec = tailor_res["structured_spec"]
        initial_score = tailor_res["ats_score"]
        initial_report = tailor_res["ats_report"]
        
        # Step 2: Evaluation Check
        missing_skills = initial_report.get("missing_skills", [])
        weak_skills = initial_report.get("weak_skills", [])
        target_optimization_keywords = list(set(missing_skills + weak_skills))[:5] # limit keywords to inject
        
        if initial_score >= 90 or not target_optimization_keywords:
            logger.info(f"Initial tailoring achieved high score ({initial_score}%). Skipping optimization pass.")
            return tailor_res

        # Step 3: Run Optimization Pass (Bullet Optimization Loop)
        logger.info(f"Targeting optimization. Score: {initial_score}%. Keywords: {target_optimization_keywords}")
        
        optimized_spec = dict(initial_spec)
        optimized_experiences = []
        
        for exp in optimized_spec.get("experiences", []):
            original_bullets = exp.get("bullet_points", [])
            # Optimize bullets using target keywords
            improved_bullets = self.optimizer_agent.optimize_bullets(original_bullets, target_optimization_keywords)
            
            exp_copy = dict(exp)
            exp_copy["bullet_points"] = improved_bullets
            optimized_experiences.append(exp_copy)
            
        optimized_spec["experiences"] = optimized_experiences

        # Step 4: Re-evaluate Optimized Spec
        template = ResumeTemplate.objects.filter(name=template_name, is_active=True).first()
        if not template:
            template = get_or_create_default_template()

        optimized_latex = ResumeTemplateRenderer.render(template, optimized_spec)
        
        # Create temp version for analyzer validation
        temp_version = ResumeVersion(
            user_profile=user_profile,
            job_posting=job_posting,
            resume_template=template,
            structured_spec=optimized_spec,
            latex_code=optimized_latex
        )
        
        optimized_report = ATSGapAnalyzer.analyze(user_profile, job_posting, resume_version=temp_version)
        optimized_score = optimized_report["match_score"]

        # Step 5: Score Comparison & Selection
        final_spec = initial_spec
        final_report = initial_report
        final_latex = tailor_res["latex_code"]
        final_pdf = tailor_res["pdf_path"]
        final_score = initial_score

        if optimized_score > initial_score:
            logger.info(f"Optimization loop successful. Score improved: {initial_score}% ➔ {optimized_score}%")
            final_spec = optimized_spec
            final_report = optimized_report
            final_latex = optimized_latex
            final_pdf = LaTeXEngine.compile_pdf(optimized_latex)
            final_score = optimized_score
        else:
            logger.info("Optimization pass did not improve ATS score. Rolling back to initial spec.")

        # Update the active ResumeVersion in the database
        resume_version_id = tailor_res["resume_version_id"]
        version = ResumeVersion.objects.get(id=resume_version_id)
        version.structured_spec = final_spec
        version.latex_code = final_latex
        version.pdf_path = final_pdf
        version.ats_score = final_score
        version.generation_latency_ms = int((time.time() - start_time) * 1000)
        version.save()

        # Update the ATSReport
        ATSGapAnalyzer.analyze(user_profile, job_posting, resume_version=version)

        return {
            "resume_version_id": str(version.id),
            "version_name": version.version_name,
            "ats_score": final_score,
            "structured_spec": final_spec,
            "latex_code": final_latex,
            "pdf_path": final_pdf,
            "ats_report": final_report,
            "generation_latency_ms": version.generation_latency_ms
        }
