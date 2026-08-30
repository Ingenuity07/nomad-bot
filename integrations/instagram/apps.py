from django.apps import AppConfig


class InstagramConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integrations.instagram'
    label = 'instagram_integration'
    verbose_name = 'Instagram Integration'
