import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0009_socialpostversion_quality_check"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="brandprofile",
            name="performance_rules",
            field=models.JSONField(blank=True, default=list, help_text="Evidence-backed content rules explicitly accepted by a workspace member."),
        ),
        migrations.CreateModel(
            name="SocialMetricObservation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider", models.CharField(choices=[("MANUAL", "Manual"), ("BUFFER", "Buffer (legacy)"), ("N8N", "n8n (legacy)"), ("WEBHOOK", "Webhook (legacy)"), ("UPLOAD_POST", "Upload Post"), ("ZERNIO", "Zernio")], max_length=20)),
                ("metric_name", models.CharField(choices=[("IMPRESSIONS", "Impressions"), ("VIEWS", "Views"), ("REACTIONS", "Reactions"), ("LIKES", "Likes"), ("COMMENTS", "Comments"), ("SHARES", "Shares"), ("REPOSTS", "Reposts"), ("CLICKS", "Clicks"), ("FOLLOWER_GROWTH", "Follower growth")], db_index=True, max_length=30)),
                ("value", models.BigIntegerField()),
                ("measured_at", models.DateTimeField(db_index=True)),
                ("raw_reference", models.JSONField(blank=True, default=dict)),
                ("ingestion_key", models.CharField(editable=False, max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("connection", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="metric_observations", to="social_content.socialconnection")),
                ("publish_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="metric_observations", to="social_content.publishjob")),
                ("variant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="metric_observations", to="social_content.socialpostvariant")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="social_metric_observations", to="prospecting.workspace")),
            ],
            options={
                "ordering": ["-measured_at", "metric_name"],
                "indexes": [models.Index(fields=["workspace", "metric_name", "measured_at"], name="social_metric_workspace_idx"), models.Index(fields=["variant", "metric_name", "measured_at"], name="social_metric_variant_idx")],
                "constraints": [models.CheckConstraint(condition=models.Q(("variant__isnull", False), ("connection__isnull", False), _connector="OR"), name="social_metric_has_subject")],
            },
        ),
        migrations.CreateModel(
            name="AnalyticsSuggestion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("fingerprint", models.CharField(max_length=64)),
                ("dimension", models.CharField(max_length=30)),
                ("segment", models.CharField(max_length=255)),
                ("suggested_rule", models.CharField(max_length=500)),
                ("rationale", models.TextField()),
                ("evidence", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("ACCEPTED", "Accepted"), ("DISMISSED", "Dismissed")], db_index=True, default="PENDING", max_length=20)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("applied_brand_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="analytics_suggestions", to="social_content.brandprofileversion")),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="decided_social_analytics_suggestions", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="social_analytics_suggestions", to="prospecting.workspace")),
            ],
            options={
                "ordering": ["status", "-created_at"],
                "constraints": [models.UniqueConstraint(fields=("workspace", "fingerprint"), name="unique_workspace_analytics_suggestion")],
            },
        ),
    ]
