from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0002_import_linkedin_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialworkspacesettings",
            name="disabled_publishing_providers",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Publishing providers disabled for this workspace.",
            ),
        ),
        migrations.AddField(
            model_name="socialworkspacesettings",
            name="publishing_provider_override",
            field=models.CharField(
                blank=True,
                choices=[("UPLOAD_POST", "Upload Post"), ("ZERNIO", "Zernio")],
                default="",
                help_text="Administrator-only override for new connections and publish jobs.",
                max_length=20,
            ),
        ),
    ]
