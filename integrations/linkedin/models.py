import uuid
from datetime import time

from django.db import models

from prospecting.models import Workspace


class LinkedInAutomationSettings(models.Model):
    APPROVAL_REQUIRED = "REQUIRE_APPROVAL"
    AUTO_PUBLISH = "AUTO_PUBLISH"
    APPROVAL_CHOICES = [
        (APPROVAL_REQUIRED, "Require approval"),
        (AUTO_PUBLISH, "Publish automatically"),
    ]

    MANUAL = "MANUAL"
    WEBHOOK = "WEBHOOK"
    PUBLISHER_CHOICES = [
        (MANUAL, "Manual handoff"),
        (WEBHOOK, "Approved provider webhook"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.OneToOneField(
        Workspace,
        on_delete=models.CASCADE,
        related_name="linkedin_automation_settings",
    )
    page_name = models.CharField(max_length=255, default="Route Floww")
    company_description = models.TextField(blank=True, default="")
    audience = models.TextField(blank=True, default="")
    brand_voice = models.CharField(max_length=255, default="Clear, practical and optimistic")
    content_pillars = models.JSONField(default=list, blank=True)
    calls_to_action = models.JSONField(default=list, blank=True)
    forbidden_topics = models.JSONField(default=list, blank=True)
    image_style = models.TextField(blank=True, default="Editorial illustration, clean shapes, brand green accents")
    language = models.CharField(max_length=50, default="English")
    timezone = models.CharField(max_length=100, default="Asia/Kolkata")
    schedule_days = models.JSONField(default=list, blank=True)
    post_time = models.TimeField(default=time(10, 0))
    posts_per_week = models.PositiveSmallIntegerField(default=5)
    queue_horizon_days = models.PositiveSmallIntegerField(default=14)
    approval_mode = models.CharField(
        max_length=30,
        choices=APPROVAL_CHOICES,
        default=APPROVAL_REQUIRED,
    )
    publisher = models.CharField(
        max_length=20,
        choices=PUBLISHER_CHOICES,
        default=MANUAL,
    )
    is_active = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "LinkedIn automation settings"

    def __str__(self):
        return f"{self.page_name} LinkedIn automation"


class ContentBrief(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    settings = models.ForeignKey(
        LinkedInAutomationSettings,
        on_delete=models.CASCADE,
        related_name="briefs",
    )
    context = models.TextField()
    label = models.CharField(max_length=255, blank=True, default="")
    is_evergreen = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label or self.context[:60]


class LinkedInPost(models.Model):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    READY = "READY"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Awaiting approval"),
        (SCHEDULED, "Scheduled"),
        (READY, "Ready for manual posting"),
        (PUBLISHING, "Publishing"),
        (PUBLISHED, "Published"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    settings = models.ForeignKey(
        LinkedInAutomationSettings,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    brief = models.ForeignKey(
        ContentBrief,
        on_delete=models.SET_NULL,
        related_name="posts",
        null=True,
        blank=True,
    )
    topic = models.CharField(max_length=255)
    hook = models.CharField(max_length=500, blank=True, default="")
    body = models.TextField()
    hashtags = models.JSONField(default=list, blank=True)
    image_prompt = models.TextField(blank=True, default="")
    image_url = models.URLField(max_length=2000, blank=True, default="")
    image_data = models.BinaryField(blank=True, default=bytes, editable=False)
    image_content_type = models.CharField(max_length=100, blank=True, default="image/png")
    alt_text = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    scheduled_for = models.DateTimeField(db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    external_post_id = models.CharField(max_length=500, blank=True, default="")
    failure_reason = models.TextField(blank=True, default="")
    generation_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for", "created_at"]
        indexes = [
            models.Index(fields=["status", "scheduled_for"], name="linkedin_po_status_74442a_idx"),
            models.Index(fields=["settings", "scheduled_for"], name="linkedin_po_setting_5938f6_idx"),
        ]

    def __str__(self):
        return f"{self.topic} ({self.status})"
