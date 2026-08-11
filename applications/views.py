import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from knowledge_base.models import UserProfile, JobPosting
from resume.models import ResumeVersion
from applications.models import ApplicationTracker
from prospecting.views import get_default_user

logger = logging.getLogger(__name__)

class ApplicationTrackerAPIView(APIView):
    """List or record job application records linked to optimized resumes."""

    def get(self, request):
        user = get_default_user()
        apps = ApplicationTracker.objects.filter(user_profile=user).order_by('-created_at')
        data = [{
            "id": str(a.id),
            "company_name": a.job_posting.company_name,
            "job_title": a.job_posting.job_title,
            "location": a.job_posting.location,
            "status": a.status,
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            "notes": a.notes,
            "resume_version": {
                "id": str(a.resume_version.id),
                "version_name": a.resume_version.version_name,
                "ats_score": a.resume_version.ats_score
            } if a.resume_version else None,
            "created_at": a.created_at.isoformat()
        } for a in apps]
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        user = get_default_user()
        job_id = request.data.get("job_id")
        resume_version_id = request.data.get("resume_version_id")
        status_val = request.data.get("status", "applied")
        notes = request.data.get("notes", "")

        if not job_id:
            return Response({"error": "job_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            job = JobPosting.objects.get(id=job_id, user_profile=user)
        except JobPosting.DoesNotExist:
            return Response({"error": "Job posting not found"}, status=status.HTTP_404_NOT_FOUND)

        resume_version = None
        if resume_version_id:
            try:
                resume_version = ResumeVersion.objects.get(id=resume_version_id, user_profile=user)
            except ResumeVersion.DoesNotExist:
                return Response({"error": "Resume version not found"}, status=status.HTTP_404_NOT_FOUND)

        app = ApplicationTracker.objects.create(
            user_profile=user,
            job_posting=job,
            resume_version=resume_version,
            status=status_val,
            notes=notes
        )
        return Response({
            "id": str(app.id),
            "status": app.status
        }, status=status.HTTP_201_CREATED)
