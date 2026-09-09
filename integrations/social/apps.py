from django.apps import AppConfig


class SocialContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integrations.social"
    label = "social_content"
    verbose_name = "Social Content"

    def ready(self):
        from integrations.social import signals  # noqa: F401
