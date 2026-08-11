import uuid
from django.db import models
from knowledge_base.models import UserProfile, JobPosting

class ResumeTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    latex_source = models.TextField()  # Jinja2 template content
    preview_image = models.CharField(max_length=500, blank=True, null=True)
    supported_sections = models.JSONField(default=list)
    variables = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ResumeVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='resume_versions')
    job_posting = models.ForeignKey(JobPosting, on_delete=models.SET_NULL, null=True, blank=True, related_name='resumes')
    resume_template = models.ForeignKey(ResumeTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='resumes')
    version_name = models.CharField(max_length=255)
    template_name = models.CharField(max_length=100, default="modern")
    structured_spec = models.JSONField(default=dict)
    latex_code = models.TextField()
    pdf_path = models.CharField(max_length=500, blank=True, null=True)
    ats_score = models.IntegerField(default=0)
    generation_latency_ms = models.IntegerField(default=0)
    llm_provider = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.version_name} (ATS: {self.ats_score}%)"


class ATSReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resume_version = models.OneToOneField(ResumeVersion, on_delete=models.CASCADE, related_name='ats_report')
    match_score = models.IntegerField(default=0)
    present_skills = models.JSONField(default=list)
    missing_skills = models.JSONField(default=list)
    weak_skills = models.JSONField(default=list)
    formatting_score = models.IntegerField(default=100)
    improvement_suggestions = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ATS Report for {self.resume_version.version_name}: {self.match_score}%"
