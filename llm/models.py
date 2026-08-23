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
            if original.key != self.key:
                raise ValidationError("The key of an existing prompt cannot be changed.")
            if original.version != self.version:
                raise ValidationError("The version of an existing prompt cannot be changed directly.")

    def save(self, *args, **kwargs):
        from django.db.models import Max

        if getattr(self, '_saving_new_version', False):
            super().save(*args, **kwargs)
            return

        if not self._state.adding:
            original = LLMPrompt.objects.get(pk=self.pk)
            if original.template != self.template or original.description != self.description:
                # Mark original record as inactive in database
                LLMPrompt.objects.filter(pk=original.pk).update(is_active=False)

                # Clone this instance into a new database row
                self.pk = uuid.uuid4()
                self._state.adding = True

                # Increment version
                max_version = LLMPrompt.objects.filter(key=original.key).aggregate(Max('version'))['version__max']
                self.version = (max_version or original.version or 0) + 1
                self.is_active = True

                # Set all other versions of this key to inactive
                LLMPrompt.objects.filter(key=self.key).update(is_active=False)
            else:
                if self.is_active:
                    LLMPrompt.objects.filter(key=self.key).exclude(id=self.id).update(is_active=False)
        else:
            if self.is_active:
                LLMPrompt.objects.filter(key=self.key).exclude(id=self.id).update(is_active=False)

        self.full_clean()
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
