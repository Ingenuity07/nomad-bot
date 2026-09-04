import datetime
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("prospecting", "0018_workerruntimestate"),
    ]

    operations = [
        migrations.CreateModel(
            name="LinkedInAutomationSettings",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("page_name", models.CharField(default="Route Floww", max_length=255)),
                ("company_description", models.TextField(blank=True, default="")),
                ("audience", models.TextField(blank=True, default="")),
                ("brand_voice", models.CharField(default="Clear, practical and optimistic", max_length=255)),
                ("content_pillars", models.JSONField(blank=True, default=list)),
                ("calls_to_action", models.JSONField(blank=True, default=list)),
                ("forbidden_topics", models.JSONField(blank=True, default=list)),
                ("image_style", models.TextField(blank=True, default="Editorial illustration, clean shapes, brand green accents")),
                ("language", models.CharField(default="English", max_length=50)),
                ("timezone", models.CharField(default="Asia/Kolkata", max_length=100)),
                ("schedule_days", models.JSONField(blank=True, default=list)),
                ("post_time", models.TimeField(default=datetime.time(10, 0))),
                ("posts_per_week", models.PositiveSmallIntegerField(default=5)),
                ("queue_horizon_days", models.PositiveSmallIntegerField(default=14)),
                ("approval_mode", models.CharField(choices=[("REQUIRE_APPROVAL", "Require approval"), ("AUTO_PUBLISH", "Publish automatically")], default="REQUIRE_APPROVAL", max_length=30)),
                ("publisher", models.CharField(choices=[("MANUAL", "Manual handoff"), ("WEBHOOK", "Approved provider webhook")], default="MANUAL", max_length=20)),
                ("is_active", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("workspace", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="linkedin_automation_settings", to="prospecting.workspace")),
            ],
            options={"verbose_name_plural": "LinkedIn automation settings"},
        ),
        migrations.CreateModel(
            name="ContentBrief",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("context", models.TextField()),
                ("label", models.CharField(blank=True, default="", max_length=255)),
                ("is_evergreen", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("settings", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="briefs", to="linkedin_automation.linkedinautomationsettings")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LinkedInPost",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("topic", models.CharField(max_length=255)),
                ("hook", models.CharField(blank=True, default="", max_length=500)),
                ("body", models.TextField()),
                ("hashtags", models.JSONField(blank=True, default=list)),
                ("image_prompt", models.TextField(blank=True, default="")),
                ("image_url", models.URLField(blank=True, default="", max_length=2000)),
                ("image_data", models.BinaryField(blank=True, default=bytes, editable=False)),
                ("image_content_type", models.CharField(blank=True, default="image/png", max_length=100)),
                ("alt_text", models.CharField(blank=True, default="", max_length=500)),
                ("status", models.CharField(choices=[("DRAFT", "Awaiting approval"), ("SCHEDULED", "Scheduled"), ("READY", "Ready for manual posting"), ("PUBLISHING", "Publishing"), ("PUBLISHED", "Published"), ("FAILED", "Failed"), ("CANCELLED", "Cancelled")], db_index=True, default="DRAFT", max_length=20)),
                ("scheduled_for", models.DateTimeField(db_index=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("external_post_id", models.CharField(blank=True, default="", max_length=500)),
                ("failure_reason", models.TextField(blank=True, default="")),
                ("generation_metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brief", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="posts", to="linkedin_automation.contentbrief")),
                ("settings", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="posts", to="linkedin_automation.linkedinautomationsettings")),
            ],
            options={"ordering": ["scheduled_for", "created_at"]},
        ),
        migrations.AddIndex(
            model_name="linkedinpost",
            index=models.Index(fields=["status", "scheduled_for"], name="linkedin_po_status_74442a_idx"),
        ),
        migrations.AddIndex(
            model_name="linkedinpost",
            index=models.Index(fields=["settings", "scheduled_for"], name="linkedin_po_setting_5938f6_idx"),
        ),
    ]
