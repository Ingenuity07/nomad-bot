import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('nomad_bot')

# Configure Celery using values from Django settings with a 'CELERY_' prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover task modules from all registered Django app configs.
app.autodiscover_tasks()
