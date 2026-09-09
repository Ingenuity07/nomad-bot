from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("social_content", "0008_brand_brain_sources_story_interviews")]

    operations = [
        migrations.AddField(
            model_name="socialpostversion",
            name="quality_check",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Pre-approval checks captured for this exact immutable version.",
            ),
        ),
    ]
