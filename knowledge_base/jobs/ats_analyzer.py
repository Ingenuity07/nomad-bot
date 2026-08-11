from typing import Dict, Any
from knowledge_base.models import UserProfile, JobPosting, Skill, Experience
from resume.models import ATSReport, ResumeVersion

class ATSGapAnalyzer:
    """
    Compares a target JobPosting against a user's Professional Knowledge Base
    to produce a multi-dimensional ATS Match & Gap Analysis Report.
    """

    @staticmethod
    def analyze(user_profile: UserProfile, job_posting: JobPosting, resume_version: ResumeVersion = None) -> Dict[str, Any]:
        """Perform ATS keyword match, formatting, verb strength, and readability checks."""
        required_skills = [s.strip().lower() for s in job_posting.required_skills]
        preferred_skills = [s.strip().lower() for s in job_posting.preferred_skills]
        ats_keywords = [k.strip().lower() for k in job_posting.ats_keywords]
        
        all_target_skills = list(set(required_skills + preferred_skills + ats_keywords))
        
        # User skills in PostgreSQL Knowledge Base
        user_skills_qs = Skill.objects.filter(user_profile=user_profile)
        user_skill_names = {s.name.strip().lower(): s.proficiency for s in user_skills_qs}
        
        # Inspect experience bullet points and tech stack for matching keywords
        exp_qs = Experience.objects.filter(user_profile=user_profile)
        all_exp_text = " ".join([
            f"{e.company} {e.role} {e.summary} {' '.join(e.bullet_points)} {' '.join(e.tech_stack)}"
            for e in exp_qs
        ]).lower()

        present_skills = []
        missing_skills = []
        weak_skills = []

        if not all_target_skills:
            all_target_skills = ["python", "django", "postgresql", "docker", "api design"]

        for skill in all_target_skills:
            if skill in user_skill_names or skill in all_exp_text:
                proficiency = user_skill_names.get(skill, "Intermediate")
                if proficiency in ["Expert", "Advanced"]:
                    present_skills.append(skill.title())
                else:
                    weak_skills.append(skill.title())
            else:
                missing_skills.append(skill.title())

        # 1. Keyword match score
        total_keywords = len(all_target_skills)
        matched_count = len(present_skills) + (len(weak_skills) * 0.5)
        keyword_score = int(min(100, (matched_count / total_keywords) * 100)) if total_keywords > 0 else 85

        # 2. Verb strength score
        action_verbs = ["architected", "designed", "implemented", "optimized", "managed", "spearheaded", "engineered", "reduced", "increased", "built", "led"]
        verb_count = sum(all_exp_text.count(v) for v in action_verbs)
        verb_score = min(100, int((verb_count / max(1, len(exp_qs) * 3)) * 100))

        # 3. Readability score
        # Estimated based on experience bullet point average length (ideal is between 40 and 150 characters)
        all_bullets = []
        for e in exp_qs:
            all_bullets.extend(e.bullet_points)
        avg_len = sum(len(b) for b in all_bullets) / max(1, len(all_bullets))
        readability_score = 100 - min(40, abs(avg_len - 95))

        # 4. Formatting score (100% since compiled via dynamic LaTeX engine)
        formatting_score = 100

        # Weighted Overall Score
        overall_score = int((keyword_score * 0.5) + (verb_score * 0.2) + (readability_score * 0.1) + (formatting_score * 0.2))

        suggestions = []
        if missing_skills:
            suggestions.append(f"Consider addressing missing key requirements: {', '.join(missing_skills[:3])}")
        if weak_skills:
            suggestions.append(f"Emphasize achievements for weak skill matches: {', '.join(weak_skills[:3])}")
        if verb_score < 70:
            suggestions.append("Incorporate more active power verbs (e.g., 'Spearheaded', 'Optimized') at the start of bullets.")
        if avg_len > 180:
            suggestions.append("Some bullet points are too verbose. Try keeping them under 150 characters for better readability.")

        report_data = {
            "match_score": overall_score,
            "keyword_score": keyword_score,
            "verb_score": verb_score,
            "readability_score": int(readability_score),
            "formatting_score": formatting_score,
            "present_skills": present_skills,
            "missing_skills": missing_skills,
            "weak_skills": weak_skills,
            "improvement_suggestions": suggestions
        }

        # If a resume version is attached and already saved in the database, update its ATS score
        if resume_version and resume_version.pk:
            try:
                ResumeVersion.objects.get(pk=resume_version.pk)
                resume_version.ats_score = overall_score
                resume_version.save(update_fields=['ats_score'])
                
                ATSReport.objects.update_or_create(
                    resume_version=resume_version,
                    defaults={
                        "match_score": overall_score,
                        "present_skills": present_skills,
                        "missing_skills": missing_skills,
                        "weak_skills": weak_skills,
                        "formatting_score": formatting_score,
                        "improvement_suggestions": suggestions
                    }
                )
            except ResumeVersion.DoesNotExist:
                pass

        return report_data
