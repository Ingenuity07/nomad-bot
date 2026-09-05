from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("linkedin_automation", "0002_buffer_n8n_and_submitted")]

    operations = [
        migrations.AlterField(
            model_name="linkedinautomationsettings",
            name="page_name",
            field=models.CharField(default="Your business", max_length=255),
        ),
        migrations.AlterField(
            model_name="linkedinautomationsettings",
            name="brand_voice",
            field=models.CharField(default="Clear, credible and human", max_length=255),
        ),
        migrations.AlterField(
            model_name="linkedinautomationsettings",
            name="image_style",
            field=models.TextField(
                blank=True,
                default="Premium editorial photography or refined 3D illustration, simple composition, brand-aligned colors",
            ),
        ),
    ]
