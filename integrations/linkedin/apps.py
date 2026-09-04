from django.apps import AppConfig


class LinkedInAutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integrations.linkedin"
    label = "linkedin_automation"
    verbose_name = "LinkedIn Content Automation"

