import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("social_content", "0005_variant_media_storage")]

    operations = [
        migrations.CreateModel(
            name="ContentStudioOnboarding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("current_step", models.PositiveSmallIntegerField(default=1)),
                ("completed_steps", models.JSONField(blank=True, default=list)),
                ("answers", models.JSONField(blank=True, default=dict)),
                ("draft_only_mode", models.BooleanField(default=False)),
                ("connection_provider", models.CharField(blank=True, default="", max_length=20)),
                ("connection_state", models.CharField(blank=True, default="", max_length=500)),
                ("connection_expires_at", models.DateTimeField(blank=True, null=True)),
                ("connection_error", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("workspace", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="content_studio_onboarding", to="prospecting.workspace")),
            ],
            options={"verbose_name": "Content Studio onboarding"},
        ),
    ]
