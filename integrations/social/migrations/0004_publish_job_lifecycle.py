import django.db.models.deletion
import uuid
from django.db import migrations, models


POST_STATE_MAP = {
    "READY": "APPROVED",
}

JOB_STATE_MAP = {
    "PENDING": "SCHEDULED",
    "CLAIMED": "PUBLISHING",
    "SUCCEEDED": "PUBLISHED",
}

REVERSE_POST_STATE_MAP = {value: key for key, value in POST_STATE_MAP.items()}
REVERSE_JOB_STATE_MAP = {value: key for key, value in JOB_STATE_MAP.items()}


def populate_lifecycle_fields(apps, schema_editor):
    Variant = apps.get_model("social_content", "SocialPostVariant")
    Version = apps.get_model("social_content", "SocialPostVersion")
    Job = apps.get_model("social_content", "PublishJob")

    for variant in Variant.objects.all().iterator():
        state = POST_STATE_MAP.get(variant.status, variant.status)
        approved_version = (
            Version.objects.filter(variant_id=variant.id, approved_at__isnull=False)
            .order_by("-version")
            .first()
        )
        Variant.objects.filter(pk=variant.pk).update(
            status=state,
            approved_version_id=approved_version.id if approved_version else None,
        )
        Version.objects.filter(variant_id=variant.id, scheduled_for__isnull=True).update(
            scheduled_for=variant.scheduled_for,
        )

    for job in Job.objects.select_related("variant", "approved_version").all().iterator():
        details = job.error_details if isinstance(job.error_details, dict) else {}
        scheduled_for = job.approved_version.scheduled_for or job.variant.scheduled_for
        Job.objects.filter(pk=job.pk).update(
            connection_id=job.variant.connection_id,
            scheduled_for=scheduled_for,
            status=JOB_STATE_MAP.get(job.status, job.status),
            failure_message=str(details.get("message") or ""),
            diagnostic_details={key: value for key, value in details.items() if key != "message"},
        )


def restore_legacy_fields(apps, schema_editor):
    Variant = apps.get_model("social_content", "SocialPostVariant")
    Job = apps.get_model("social_content", "PublishJob")

    for variant in Variant.objects.all().iterator():
        Variant.objects.filter(pk=variant.pk).update(
            status=REVERSE_POST_STATE_MAP.get(variant.status, variant.status),
        )
    for job in Job.objects.all().iterator():
        details = dict(job.diagnostic_details or {})
        if job.failure_message:
            details["message"] = job.failure_message
        Job.objects.filter(pk=job.pk).update(
            status=REVERSE_JOB_STATE_MAP.get(job.status, job.status),
            error_details=details,
        )


LIFECYCLE_CHOICES = [
    ("DRAFT", "Draft"),
    ("NEEDS_REVIEW", "Needs review"),
    ("APPROVED", "Approved"),
    ("SCHEDULED", "Scheduled"),
    ("PUBLISHING", "Publishing"),
    ("SUBMITTED", "Submitted"),
    ("PUBLISHED", "Published"),
    ("FAILED", "Failed"),
    ("CANCELLED", "Cancelled"),
    ("CONNECTION_REQUIRED", "Connection required"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0003_publishing_routing_policy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="socialpost",
            name="state",
            field=models.CharField(
                choices=LIFECYCLE_CHOICES,
                db_index=True,
                default="DRAFT",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="socialpostvariant",
            name="status",
            field=models.CharField(
                choices=LIFECYCLE_CHOICES,
                db_index=True,
                default="DRAFT",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="publishjob",
            name="status",
            field=models.CharField(
                choices=LIFECYCLE_CHOICES + [("UNKNOWN", "Unknown outcome")],
                db_index=True,
                default="SCHEDULED",
                max_length=25,
            ),
        ),
        migrations.AlterField(
            model_name="providerevent",
            name="normalized_status",
            field=models.CharField(
                blank=True,
                choices=LIFECYCLE_CHOICES + [("UNKNOWN", "Unknown outcome")],
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="socialpostvariant",
            name="approved_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_variants",
                to="social_content.socialpostversion",
            ),
        ),
        migrations.AddField(
            model_name="socialpostversion",
            name="scheduled_for",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="connection",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="publish_jobs",
                to="social_content.socialconnection",
            ),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="scheduled_for",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="next_attempt_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="claim_token",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="unknown_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="failure_message",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="publishjob",
            name="diagnostic_details",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(populate_lifecycle_fields, restore_legacy_fields),
        migrations.AlterField(
            model_name="socialpostversion",
            name="scheduled_for",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="publishjob",
            name="connection",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="publish_jobs",
                to="social_content.socialconnection",
            ),
        ),
        migrations.AlterField(
            model_name="publishjob",
            name="scheduled_for",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.RemoveField(
            model_name="publishjob",
            name="error_details",
        ),
        migrations.CreateModel(
            name="PublishAttempt",
            fields=[
                (
                    "id",
                    models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
                ("attempt_number", models.PositiveSmallIntegerField()),
                (
                    "idempotency_key",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=LIFECYCLE_CHOICES + [("UNKNOWN", "Unknown outcome")],
                        db_index=True,
                        default="PUBLISHING",
                        max_length=25,
                    ),
                ),
                ("external_id", models.CharField(blank=True, default="", max_length=500)),
                ("failure_message", models.TextField(blank=True, default="")),
                ("diagnostic_details", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attempts",
                        to="social_content.publishjob",
                    ),
                ),
            ],
            options={"ordering": ["attempt_number"]},
        ),
        migrations.AddConstraint(
            model_name="publishattempt",
            constraint=models.UniqueConstraint(
                fields=("job", "attempt_number"),
                name="unique_social_publish_attempt",
            ),
        ),
        migrations.AddConstraint(
            model_name="publishjob",
            constraint=models.UniqueConstraint(
                fields=("variant", "approved_version"),
                name="unique_social_job_approval",
            ),
        ),
        migrations.AddConstraint(
            model_name="providerevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(("external_event_id", ""), _negated=True),
                fields=("provider", "external_event_id"),
                name="unique_social_provider_event",
            ),
        ),
        migrations.AddIndex(
            model_name="publishjob",
            index=models.Index(
                fields=["status", "scheduled_for", "next_attempt_at"],
                name="social_job_due_idx",
            ),
        ),
    ]
