import uuid
from django.db import models
from knowledge_base.models import UserProfile, JobPosting
from resume.models import ResumeVersion

class ApplicationTracker(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('tailored', 'Tailored'),
        ('applied', 'Applied'),
        ('interview', 'Interviewing'),
        ('rejected', 'Rejected'),
        ('offer', 'Offer Received'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='applications')
    job_posting = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name='applications')
    resume_version = models.ForeignKey(ResumeVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='applications')
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='draft')
    applied_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.job_posting.company_name} - {self.status}"
