import uuid
from django.db import models
from jinja2 import Template, TemplateSyntaxError

class LLMPrompt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=150, db_index=True)
    version = models.IntegerField(default=1)
    template = models.TextField()
    description = models.TextField(default='', blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('key', 'version')

    def __str__(self):
        return f"{self.key} (v{self.version})"

    def clean(self):
        from django.core.exceptions import ValidationError
        try:
            Template(self.template)
        except TemplateSyntaxError as err:
            raise ValidationError({"template": f"Malformed Jinja2 template syntax: {err}"})

        if not self._state.adding:
            original = LLMPrompt.objects.get(pk=self.pk)
            from llm.models import PromptRun
            has_runs = PromptRun.objects.using('telemetry').filter(prompt_key=original.key, prompt_version=original.version).exists()
            if (original.template != self.template or original.key != self.key) and has_runs:
                raise ValidationError("Historical templates are immutable once used in a PromptRun.")

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.is_active:
            LLMPrompt.objects.filter(key=self.key).exclude(id=self.id).update(is_active=False)
        super().save(*args, **kwargs)


class PromptRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=100)  # ingestion, tailoring, optimization
    prompt_text = models.TextField()
    response_text = models.TextField()
    model_name = models.CharField(max_length=100)
    temperature = models.FloatField(default=0.0)
    tokens_used = models.IntegerField(default=0)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost_usd = models.FloatField(default=0.0)
    latency_ms = models.IntegerField(default=0)
    ats_score = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Observability & Tracing Extensions
    correlation_id = models.CharField(max_length=150, default='', blank=True, db_index=True)
    trace_id = models.CharField(max_length=100, default='', blank=True, db_index=True)
    span_id = models.CharField(max_length=100, default='', blank=True)
    operation = models.CharField(max_length=150, default='', blank=True)

    prompt_version = models.IntegerField(null=True, blank=True)
    prompt_key = models.CharField(max_length=150, default='', blank=True)
    template_variables = models.JSONField(default=dict, blank=True)
    rendered_prompt = models.TextField(default='', blank=True)

    provider = models.CharField(max_length=100, default='', blank=True)
    model = models.CharField(max_length=100, default='', blank=True)

    input_cost = models.FloatField(default=0.0)
    output_cost = models.FloatField(default=0.0)
    total_cost = models.FloatField(default=0.0)

    duration_ms = models.IntegerField(default=0)

    status = models.CharField(max_length=50, default='success')  # success, error
    error_type = models.CharField(max_length=100, default='', blank=True)
    error_code = models.CharField(max_length=100, default='', blank=True)
    error_message = models.TextField(default='', blank=True)
    provider_status_code = models.IntegerField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)

    max_tokens = models.IntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

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
