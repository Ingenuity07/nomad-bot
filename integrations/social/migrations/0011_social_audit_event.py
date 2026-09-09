import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0010_mvp_analytics"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SocialAuditEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(choices=[("CONNECTION_STARTED", "Connection started"), ("CONNECTION_COMPLETED", "Connection completed"), ("CONNECTION_DISCONNECTED", "Connection disconnected"), ("APPROVAL_GRANTED", "Approval granted"), ("CHANGES_REQUESTED", "Changes requested"), ("APPROVAL_REJECTED", "Approval rejected"), ("SCHEDULE_CHANGED", "Schedule changed"), ("PUBLISH_JOB_CREATED", "Publish job created"), ("PUBLISH_STARTED", "Publish started"), ("PUBLISH_SUBMITTED", "Publish submitted"), ("PUBLISH_COMPLETED", "Publish completed"), ("PUBLISH_FAILED", "Publish failed"), ("PUBLISH_CANCELLED", "Publish cancelled"), ("PROVIDER_POLICY_CHANGED", "Provider policy changed"), ("DATA_EXPORTED", "Data exported"), ("DATA_DELETED", "Data deleted")], db_index=True, max_length=40)),
                ("target_type", models.CharField(blank=True, default="", max_length=50)),
                ("target_id", models.CharField(blank=True, default="", max_length=100)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="social_audit_events", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="social_audit_events", to="prospecting.workspace")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["workspace", "event_type", "created_at"], name="social_audit_workspace_idx")],
            },
        ),
    ]
