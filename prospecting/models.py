import uuid
from django.db import models
from knowledge_base.models import UserProfile

class DiscoveryRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='discovery_runs')
    keyword = models.CharField(max_length=200)
    location = models.CharField(max_length=200)
    status = models.CharField(max_length=50, default='pending')  # pending, running, completed, failed
    total_leads_found = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Discovery Run: {self.keyword} in {self.location} ({self.status})"


class LeadCompany(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discovery_run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name='companies')
    name = models.CharField(max_length=255)
    website = models.URLField(max_length=500, null=True, blank=True)
    phone = models.CharField(max_length=100, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    rating = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class LeadContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(LeadCompany, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(max_length=255)
    phone = models.CharField(max_length=100, null=True, blank=True)
    linkedin = models.URLField(max_length=500, null=True, blank=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    source = models.CharField(max_length=255, default='website')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} ({self.company.name})"


class WebsiteAnalysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.OneToOneField(LeadCompany, on_delete=models.CASCADE, related_name='analysis')
    description = models.TextField(null=True, blank=True)
    has_delivery = models.BooleanField(default=False)
    has_scheduling = models.BooleanField(default=False)
    needs_routing = models.BooleanField(default=False)
    fleet_size_estimate = models.CharField(max_length=100, default='unknown')
    lead_score = models.FloatField(default=0.0)
    lead_score_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analysis for {self.company.name} (Score: {self.lead_score})"
