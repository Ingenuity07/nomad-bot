import os
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from knowledge_base.models import UserProfile, ProfessionalKnowledgeBase, Experience, Project, Skill, JobPosting
from resume.models import ResumeVersion
from applications.models import ApplicationTracker
from knowledge_base.ingestion import ResumeIngestionEngine
from knowledge_base.jobs.parser import JobParser
from knowledge_base.jobs.ats_analyzer import ATSGapAnalyzer
from prospecting.views import get_default_user

logger = logging.getLogger(__name__)

class KnowledgeBaseAPIView(APIView):
    """View and update the user's Professional Knowledge Base."""
    
    def get(self, request):
        user = get_default_user()
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user)
        experiences = Experience.objects.filter(user_profile=user)
        projects = Project.objects.filter(user_profile=user)
        skills = Skill.objects.filter(user_profile=user)

        return Response({
            "id": str(kb.id),
            "title": kb.title,
            "headline": kb.headline,
            "summary": kb.summary,
            "years_of_experience": kb.years_of_experience,
            "experiences": [{
                "id": str(e.id), "company": e.company, "role": e.role,
                "start_date": e.start_date, "end_date": e.end_date,
                "bullet_points": e.bullet_points, "tech_stack": e.tech_stack
            } for e in experiences],
            "projects": [{
                "id": str(p.id), "title": p.title, "description": p.description,
                "tech_stack": p.tech_stack, "impact_metrics": p.impact_metrics
            } for p in projects],
            "skills": [{
                "id": str(s.id), "category": s.category, "name": s.name, "proficiency": s.proficiency
            } for s in skills]
        }, status=status.HTTP_200_OK)

    def post(self, request):
        user = get_default_user()
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user)
        kb.title = request.data.get("title", kb.title)
        kb.headline = request.data.get("headline", kb.headline)
        kb.summary = request.data.get("summary", kb.summary)
        kb.years_of_experience = request.data.get("years_of_experience", kb.years_of_experience)
        kb.save()
        return Response({"status": "updated"}, status=status.HTTP_200_OK)


class ResumeIngestAPIView(APIView):
    """Parse raw resume text or uploaded PDF/DOCX/MD documents into structured Knowledge Base entities."""

    def post(self, request):
        user = get_default_user()
        uploaded_file = request.FILES.get("file")
        raw_text = request.data.get("resume_text", "")

        if not uploaded_file and not raw_text:
            return Response({"error": "file or resume_text is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Handle uploaded file extraction
        if uploaded_file:
            import tempfile
            from knowledge_base.documents.loader import DocumentLoader
            from knowledge_base.documents.ocr import OCRService

            ext = os.path.splitext(uploaded_file.name)[1].lower()
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
                for chunk in uploaded_file.chunks():
                    temp_file.write(chunk)
                temp_file_path = temp_file.name

            try:
                # Load text
                extracted = DocumentLoader.load(temp_file_path)
                text = extracted.get("text", "")

                # OCR check if needed
                ocr_service = OCRService()
                if not ocr_service.is_searchable(text):
                    text = ocr_service.extract_via_ocr(temp_file_path)
            finally:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
        else:
            text = raw_text

        if not text.strip():
            return Response({"error": "No parseable text extracted from the document"}, status=status.HTTP_400_BAD_REQUEST)

        engine = ResumeIngestionEngine()
        try:
            res = engine.parse_and_ingest(user, text)
            return Response(res, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class KnowledgeBaseResetAPIView(APIView):
    """Resets the user's Professional Knowledge Base, experiences, projects, skills, versions, and application records."""

    def post(self, request):
        user = get_default_user()
        
        # Clear everything linked to the user
        Experience.objects.filter(user_profile=user).delete()
        Project.objects.filter(user_profile=user).delete()
        Skill.objects.filter(user_profile=user).delete()
        ResumeVersion.objects.filter(user_profile=user).delete()
        ApplicationTracker.objects.filter(user_profile=user).delete()
        
        # Reset Knowledge Base details
        kb, _ = ProfessionalKnowledgeBase.objects.get_or_create(user_profile=user)
        kb.title = ""
        kb.headline = ""
        kb.summary = ""
        kb.years_of_experience = 0
        kb.save()
        
        return Response({"status": "reset_completed"}, status=status.HTTP_200_OK)


class KnowledgeBaseEnrichAPIView(APIView):
    """Enriches the user's Professional Knowledge Base by adding experience, project, or skill items."""

    def post(self, request):
        user = get_default_user()
        
        experience_data = request.data.get("experience")
        project_data = request.data.get("project")
        skill_data = request.data.get("skill")
        
        if experience_data:
            Experience.objects.create(
                user_profile=user,
                company=experience_data.get("company"),
                role=experience_data.get("role"),
                location=experience_data.get("location", ""),
                start_date=experience_data.get("start_date", ""),
                end_date=experience_data.get("end_date", ""),
                bullet_points=experience_data.get("bullet_points", []),
                tech_stack=experience_data.get("tech_stack", [])
            )
            
        if project_data:
            Project.objects.create(
                user_profile=user,
                title=project_data.get("title"),
                description=project_data.get("description", ""),
                tech_stack=project_data.get("tech_stack", []),
                impact_metrics=project_data.get("impact_metrics", [])
            )
            
        if skill_data:
            Skill.objects.create(
                user_profile=user,
                category=skill_data.get("category", "General"),
                name=skill_data.get("name"),
                proficiency=skill_data.get("proficiency", "Intermediate")
            )
            
        return Response({"status": "enrichment_completed"}, status=status.HTTP_200_OK)


class JobParseAPIView(APIView):
    """Parse a raw job description and compute initial ATS match score."""

    def post(self, request):
        user = get_default_user()
        raw_job_text = request.data.get("job_text", "")
        job_url = request.data.get("job_url", "")
        
        if not raw_job_text:
            return Response({"error": "job_text is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        parser = JobParser()
        try:
            job_posting = parser.parse_and_save(user, raw_job_text, job_url=job_url)
            ats_analysis = ATSGapAnalyzer.analyze(user, job_posting)
            
            return Response({
                "job_id": str(job_posting.id),
                "company_name": job_posting.company_name,
                "job_title": job_posting.job_title,
                "required_skills": job_posting.required_skills,
                "ats_keywords": job_posting.ats_keywords,
                "ats_analysis": ats_analysis
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
