from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("social_content", "0006_content_studio_onboarding"),
    ]

    operations = [
        migrations.AddField(
            model_name="socialpostvariant",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Platform-specific presentation data such as thread segments or carousel copy.",
            ),
        ),
        migrations.AddField(
            model_name="socialpostversion",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
