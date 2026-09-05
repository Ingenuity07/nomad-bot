from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("linkedin_automation", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="linkedinautomationsettings",
            name="publisher",
            field=models.CharField(
                choices=[
                    ("MANUAL", "Manual handoff"),
                    ("BUFFER", "Buffer"),
                    ("N8N", "n8n workflow"),
                    ("WEBHOOK", "Legacy provider webhook"),
                ],
                default="MANUAL",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="linkedinpost",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Awaiting approval"),
                    ("SCHEDULED", "Scheduled"),
                    ("READY", "Ready for manual posting"),
                    ("PUBLISHING", "Publishing"),
                    ("SUBMITTED", "Sent to publisher"),
                    ("PUBLISHED", "Published"),
                    ("FAILED", "Failed"),
                    ("CANCELLED", "Cancelled"),
                ],
                db_index=True,
                default="DRAFT",
                max_length=20,
            ),
        ),
    ]
