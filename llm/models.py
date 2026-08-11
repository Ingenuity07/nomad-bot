import uuid
from django.db import models

class PromptRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=100)  # ingestion, tailoring, optimization
    prompt_text = models.TextField()
    response_text = models.TextField()
    model_name = models.CharField(max_length=100)
    temperature = models.FloatField(default=0.0)
    tokens_used = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    ats_score = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.purpose} run on {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class AgentConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    system_prompt = models.TextField()
    model_tier = models.CharField(max_length=50, default='critical') # simple, medium, critical
    temperature = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
